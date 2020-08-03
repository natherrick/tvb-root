# -*- coding: utf-8 -*-
#
#
# TheVirtualBrain-Framework Package. This package holds all Data Management, and 
# Web-UI helpful to run brain-simulations. To use it, you also need do download
# TheVirtualBrain-Scientific Package (for simulators). See content of the
# documentation-folder for more details. See also http://www.thevirtualbrain.org
#
# (c) 2012-2020, Baycrest Centre for Geriatric Care ("Baycrest") and others
#
# This program is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or (at your option) any later version.
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE.  See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this
# program.  If not, see <http://www.gnu.org/licenses/>.
#
#
#   CITATION:
# When using The Virtual Brain for scientific publications, please cite it as follows:
#
#   Paula Sanz Leon, Stuart A. Knock, M. Marmaduke Woodman, Lia Domide,
#   Jochen Mersmann, Anthony R. McIntosh, Viktor Jirsa (2013)
#       The Virtual Brain: a simulator of primate brain network dynamics.
#   Frontiers in Neuroinformatics (7:10. doi: 10.3389/fninf.2013.00010)
#
#

"""
.. moduleauthor:: Adrian Dordea <adrian.dordea@codemart.ro>
.. moduleauthor:: Lia Domide <lia.domide@codemart.ro>
.. moduleauthor:: Calin Pavel <calin.pavel@codemart.ro>
.. moduleauthor:: Bogdan Neacsa <bogdan.neacsa@codemart.ro>
"""

import os
import shutil
from cgi import FieldStorage
from datetime import datetime
from cherrypy._cpreqbody import Part
from sqlalchemy.orm.attributes import manager_of_class
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from tvb.basic.profile import TvbProfile
from tvb.basic.logger.builder import get_logger
from tvb.config import VIEW_MODEL2ADAPTER
from tvb.config.algorithm_categories import UploadAlgorithmCategoryConfig
from tvb.core.entities.file.simulator.burst_configuration_h5 import BurstConfigurationH5
from tvb.core.entities.model.model_datatype import DataTypeGroup
from tvb.core.entities.model.model_operation import ResultFigure, Operation, STATUS_FINISHED
from tvb.core.entities.model.model_project import Project
from tvb.core.entities.storage import dao, transactional
from tvb.core.entities.model.model_burst import BurstConfiguration
from tvb.core.entities.file.xml_metadata_handlers import XMLReader
from tvb.core.entities.file.files_helper import FilesHelper
from tvb.core.entities.file.files_update_manager import FilesUpdateManager
from tvb.core.entities.file.exceptions import FileStructureException, MissingDataSetException
from tvb.core.entities.file.exceptions import IncompatibleFileManagerException
from tvb.core.neotraits.db import HasTraitsIndex
from tvb.core.services.exceptions import ImportException, ServicesBaseException
from tvb.core.services.algorithm_service import AlgorithmService
from tvb.core.project_versions.project_update_manager import ProjectUpdateManager
from tvb.core.neocom import h5
from tvb.core.neotraits._h5core import H5File, ViewModelH5


class ImportService(object):
    """
    Service for importing TVB entities into system.
    It supports TVB exported H5 files as input, but it should also handle H5 files
    generated outside of TVB, as long as they respect the same structure.
    """

    def __init__(self):
        self.logger = get_logger(__name__)
        self.user_id = None
        self.files_helper = FilesHelper()
        self.created_projects = []

    def _download_and_unpack_project_zip(self, uploaded, uq_file_name, temp_folder):

        if isinstance(uploaded, FieldStorage) or isinstance(uploaded, Part):
            if not uploaded.file:
                raise ImportException("Please select the archive which contains the project structure.")
            with open(uq_file_name, 'wb') as file_obj:
                self.files_helper.copy_file(uploaded.file, file_obj)
        else:
            shutil.copy2(uploaded, uq_file_name)

        try:
            self.files_helper.unpack_zip(uq_file_name, temp_folder)
        except FileStructureException as excep:
            self.logger.exception(excep)
            raise ImportException("Bad ZIP archive provided. A TVB exported project is expected!")

    @staticmethod
    def _compute_unpack_path():
        """
        :return: the name of the folder where to expand uploaded zip
        """
        now = datetime.now()
        date_str = "%d-%d-%d_%d-%d-%d_%d" % (now.year, now.month, now.day, now.hour,
                                             now.minute, now.second, now.microsecond)
        uq_name = "%s-ImportProject" % date_str
        return os.path.join(TvbProfile.current.TVB_TEMP_FOLDER, uq_name)

    @transactional
    def import_project_structure(self, uploaded, user_id):
        """
        Execute import operations:

        1. check if ZIP or folder
        2. find all project nodes
        3. for each project node:
            - create project
            - create all operations and groups
            - import all images
            - create all dataTypes
        """

        self.user_id = user_id
        self.created_projects = []

        # Now compute the name of the folder where to explode uploaded ZIP file
        temp_folder = self._compute_unpack_path()
        uq_file_name = temp_folder + ".zip"

        try:
            self._download_and_unpack_project_zip(uploaded, uq_file_name, temp_folder)
            self._import_projects_from_folder(temp_folder)

        except Exception as excep:
            self.logger.exception("Error encountered during import. Deleting projects created during this operation.")
            # Remove project folders created so far.
            # Note that using the project service to remove the projects will not work,
            # because we do not have support for nested transaction.
            # Removing from DB is not necessary because in transactional env a simple exception throw
            # will erase everything to be inserted.
            for project in self.created_projects:
                project_path = os.path.join(TvbProfile.current.TVB_STORAGE, FilesHelper.PROJECTS_FOLDER, project.name)
                shutil.rmtree(project_path)
            raise ImportException(str(excep))

        finally:
            # Now delete uploaded file
            if os.path.exists(uq_file_name):
                os.remove(uq_file_name)
            # Now delete temporary folder where uploaded ZIP was exploded.
            if os.path.exists(temp_folder):
                shutil.rmtree(temp_folder)

    def _import_projects_from_folder(self, temp_folder):
        """
        Process each project from the uploaded pack, to extract names.
        """
        project_roots = []
        for root, _, files in os.walk(temp_folder):
            if FilesHelper.TVB_PROJECT_FILE in files:
                project_roots.append(root)

        for temp_project_path in project_roots:
            update_manager = ProjectUpdateManager(temp_project_path)
            update_manager.run_all_updates()
            project = self.__populate_project(temp_project_path)
            # Populate the internal list of create projects so far, for cleaning up folders, in case of failure
            self.created_projects.append(project)
            # Ensure project final folder exists on disk
            project_path = self.files_helper.get_project_folder(project)
            shutil.move(os.path.join(temp_project_path, FilesHelper.TVB_PROJECT_FILE), project_path)
            # Now import project operations with their results
            self.import_project_operations(project, temp_project_path)
            # Import images and move them from temp into target
            self._store_imported_images(project, temp_project_path, project.name)

    def _load_datatypes_from_operation_folder(self, src_op_path, operation_entity, datatype_group, new_op_folder):
        """
        Loads datatypes from operation folder
        :returns: Datatype entities list
        """
        all_datatypes = []
        for file_name in os.listdir(src_op_path):
            if file_name.endswith(FilesHelper.TVB_STORAGE_FILE_EXTENSION):
                h5_file = os.path.join(src_op_path, file_name)
                try:
                    file_update_manager = FilesUpdateManager()
                    file_update_manager.upgrade_file(h5_file)
                    datatype = self.load_datatype_from_file(src_op_path, file_name, operation_entity.id,
                                                            datatype_group, final_storage=new_op_folder,
                                                            current_project_id=operation_entity.fk_launched_in)
                    all_datatypes.append(datatype)

                except IncompatibleFileManagerException:
                    os.remove(h5_file)
                    self.logger.warning("Incompatible H5 file will be ignored: %s" % h5_file)
                    self.logger.exception("Incompatibility details ...")
        return all_datatypes

    def _store_imported_datatypes_in_db(self, project, all_datatypes):
        def by_time(dt):
            return dt.create_date or datetime.now()

        all_datatypes.sort(key=by_time)
        for datatype in all_datatypes:
            datatype_already_in_tvb = dao.get_datatype_by_gid(datatype.gid)
            if not datatype_already_in_tvb:
                self.store_datatype(datatype)
            else:
                AlgorithmService.create_link([datatype_already_in_tvb.id], project.id)

    def _store_imported_images(self, project, temp_project_path, project_name):
        """
        Import all images from project
        """
        images_root = os.path.join(temp_project_path, FilesHelper.IMAGES_FOLDER)
        target_images_path = self.files_helper.get_images_folder(project_name)
        for root, _, files in os.walk(images_root):
            for metadata_file in files:
                if metadata_file.endswith(FilesHelper.TVB_FILE_EXTENSION):
                    self._import_image(root, metadata_file, project.id, target_images_path)

    def get_directory_ordered_list(self, project, import_path):
        directory_list = {}
        for root, _, files in os.walk(import_path):
            if "Operation.xml" in files and root not in directory_list.keys():
                operation_file_path = os.path.join(root, "Operation.xml")
                operation = self.__build_operation_from_file(project, operation_file_path)
                directory_list[root] = operation.create_date
            else:
                for file in files:
                    if file.endswith(FilesHelper.TVB_STORAGE_FILE_EXTENSION) and root not in directory_list.keys():
                        h5_file = os.path.join(root, file)
                        try:
                            h5_class = H5File.h5_class_from_file(h5_file)
                            if h5_class is ViewModelH5:
                                create_date = H5File.get_metadata_param(h5_file, "create_date")
                                directory_list[root] = create_date
                        except Exception as e:
                            self.logger.warning("Unreadable H5 file will be ignored: %s" % h5_file)
        directory_ordered_list = dict(sorted(directory_list.items(), key=lambda tup: tup[1]))
        return list(directory_ordered_list.keys())

    def import_project_operations(self, project, import_path):
        """
        This method scans provided folder and identify all operations that needs to be imported
        """
        imported_operations = []
        import_path = "C:\\Users\\adrian.dordea\\PycharmProjects\\TestDefaultData\\Default_Project - Copy"
        directory_ordered_list = self.get_directory_ordered_list(project, import_path)

        for path in directory_ordered_list:
            for root, _, files in os.walk(path):
                # We should order by Op (in case of XML) Create Date or ViewModel.create_date
                if "Operation.xml" in files:
                    # Previous Operation format for uploading previous versions of projects
                    operation_file_path = os.path.join(root, "Operation.xml")
                    operation = self.__build_operation_from_file(project, operation_file_path)
                    operation.import_file = operation_file_path
                    self.logger.debug("Importing operation " + str(operation))
                    operation_entity, datatype_group = self.__import_operation(operation)
                    new_op_folder = self.files_helper.get_project_folder(project, str(operation_entity.id))
                    # TODO ViewModel H5 should be created, as we want to preserve only the new structure in the future
                    operation_datatypes = self._load_datatypes_from_operation_folder(root, operation_entity,
                                                                                     datatype_group, new_op_folder)
                    self._store_imported_datatypes_in_db(project, operation_datatypes)
                    imported_operations.append(operation_entity)

                else:
                    main_view_model = None
                    dt_paths = []
                    all_view_model_files = []
                    for file in files:
                        if file.endswith(FilesHelper.TVB_STORAGE_FILE_EXTENSION):
                            h5_file = os.path.join(root, file)
                            try:
                                h5_class = H5File.h5_class_from_file(h5_file)
                                if h5_class is ViewModelH5:
                                    all_view_model_files.append(h5_file)
                                    if not main_view_model:
                                        view_model = h5.load_view_model_from_file(h5_file)
                                        if type(view_model) in VIEW_MODEL2ADAPTER.keys():
                                            main_view_model = view_model
                                else:
                                    file_update_manager = FilesUpdateManager()
                                    file_update_manager.upgrade_file(h5_file)
                                    dt_paths.append(h5_file)
                            except Exception as e:
                                self.logger.warning("Unreadable H5 file will be ignored: %s" % h5_file)

                    if main_view_model is not None:
                        alg = VIEW_MODEL2ADAPTER[type(main_view_model)]
                        operation = Operation(project.fk_admin, project.id, alg.id,
                                              parameters='{"gid": "' + main_view_model.gid.hex + '"}',
                                              meta='{"from": "Import"}', status=STATUS_FINISHED,
                                              start_date=datetime.now(), completion_date=datetime.now())
                        operation_entity = dao.store_entity(operation)
                        dt_group = None  # TODO
                        imported_operations.append(operation_entity)
                        # Now we know the target Ope Folder, we move all ViewModel H5 files there
                        new_op_folder = self.files_helper.get_project_folder(project, str(operation_entity.id))
                        for h5_file in all_view_model_files:
                            shutil.move(h5_file, new_op_folder)
                        # Store the DataTypes in db
                        if dt_paths:
                            dts = []
                            for dt_path in dt_paths:
                                folder, filename = os.path.split(dt_path)
                                dt = self.load_datatype_from_file(folder, filename, operation_entity.id,
                                                                  datatype_group=dt_group, final_storage=new_op_folder,
                                                                  current_project_id=project.id)
                                if isinstance(dt, BurstConfiguration):
                                    dao.store_entity(dt)
                                else:
                                    dts.append(dt)
                            self._store_imported_datatypes_in_db(project, dts)
                    else:
                        self.logger.warning(
                            "Folder %s will be ignored, as we could not find a main ViewModel serialized" % root)

        return imported_operations

    def _import_image(self, src_folder, metadata_file, project_id, target_images_path):
        """
        Create and store a image entity.
        """
        figure_dict = XMLReader(os.path.join(src_folder, metadata_file)).read_metadata()
        actual_figure = os.path.join(src_folder, os.path.split(figure_dict['file_path'])[1])
        if not os.path.exists(actual_figure):
            self.logger.warning("Expected to find image path %s .Skipping" % actual_figure)
            return
        # TODO: this will never match in the current form. What to do ?
        op = dao.get_operation_by_gid(figure_dict['fk_from_operation'])
        figure_dict['fk_op_id'] = op.id if op is not None else None
        figure_dict['fk_user_id'] = self.user_id
        figure_dict['fk_project_id'] = project_id
        figure_entity = manager_of_class(ResultFigure).new_instance()
        figure_entity = figure_entity.from_dict(figure_dict)
        stored_entity = dao.store_entity(figure_entity)

        # Update image meta-data with the new details after import
        figure = dao.load_figure(stored_entity.id)
        shutil.move(actual_figure, target_images_path)
        self.logger.debug("Store imported figure")
        self.files_helper.write_image_metadata(figure)

    def load_datatype_from_file(self, storage_folder, file_name, op_id, datatype_group=None,
                                move=True, final_storage=None, current_project_id=None):
        # type: (str, str, int, DataTypeGroup, bool, str, int) -> HasTraitsIndex
        """
        Creates an instance of datatype from storage / H5 file 
        :returns: DatatypeIndex
        """
        self.logger.debug("Loading DataType from file: %s" % file_name)
        current_file = os.path.join(storage_folder, file_name)
        h5_class = H5File.h5_class_from_file(current_file)
        if h5_class is BurstConfigurationH5:
            if current_project_id is None:
                op_entity = dao.get_operationgroup_by_id(op_id)
                current_project_id = op_entity.fk_launched_in
            h5_file = BurstConfigurationH5(current_file)
            burst = BurstConfiguration(current_project_id)
            burst.fk_simulation = op_id
            # burst.fk_operation_group = TODO
            # burst.fk_metric_operation_group = TODO
            h5_file.load_into(burst)
            result = burst
        else:
            datatype, generic_attributes = h5.load_with_links(current_file)
            index_class = h5.REGISTRY.get_index_for_datatype(datatype.__class__)
            datatype_index = index_class()
            # TODO datatype_index.fk_parent_burst should be GUID not ID
            datatype_index.fill_from_has_traits(datatype)
            datatype_index.fill_from_generic_attributes(generic_attributes)

            # Add all the required attributes
            if datatype_group is not None:
                datatype_index.fk_datatype_group = datatype_group.id
            datatype_index.fk_from_operation = op_id

            associated_file = h5.path_for_stored_index(datatype_index)
            if os.path.exists(associated_file):
                datatype_index.disk_size = FilesHelper.compute_size_on_disk(associated_file)
            result = datatype_index

        # Now move storage file into correct folder if necessary
        if move and final_storage is not None:
            final_path = h5.path_for(final_storage, h5_class, result.gid)
            if final_path != current_file and move:
                shutil.move(current_file, final_path)

        return result

    def store_datatype(self, datatype):
        """This method stores data type into DB"""
        try:
            self.logger.debug("Store datatype: %s with Gid: %s" % (datatype.__class__.__name__, datatype.gid))
            return dao.store_entity(datatype)
        except MissingDataSetException as e:
            self.logger.exception(e)
            error_msg = "Datatype %s has missing data and could not be imported properly." % (datatype,)
            raise ImportException(error_msg)
        except IntegrityError as excep:
            self.logger.exception(excep)
            error_msg = "Could not import data with gid: %s. There is already a one with " \
                        "the same name or gid." % datatype.gid
            raise ImportException(error_msg)

    def __populate_project(self, project_path):
        """
        Create and store a Project entity.
        """
        self.logger.debug("Creating project from path: %s" % project_path)
        project_dict = self.files_helper.read_project_metadata(project_path)

        project_entity = manager_of_class(Project).new_instance()
        project_entity = project_entity.from_dict(project_dict, self.user_id)

        try:
            self.logger.debug("Storing imported project")
            return dao.store_entity(project_entity)
        except IntegrityError as excep:
            self.logger.exception(excep)
            error_msg = ("Could not import project: %s with gid: %s. There is already a "
                         "project with the same name or gid.") % (project_entity.name, project_entity.gid)
            raise ImportException(error_msg)

    def __build_operation_from_file(self, project, operation_file):
        """
        Create Operation entity from metadata file.
        """
        operation_dict = XMLReader(operation_file).read_metadata()
        operation_entity = manager_of_class(Operation).new_instance()
        return operation_entity.from_dict(operation_dict, dao, self.user_id, project.gid)

    @staticmethod
    def __import_operation(operation_entity):
        """
        Store a Operation entity.
        """
        operation_entity = dao.store_entity(operation_entity)
        operation_group_id = operation_entity.fk_operation_group
        datatype_group = None

        if operation_group_id is not None:
            try:
                datatype_group = dao.get_datatypegroup_by_op_group_id(operation_group_id)
            except SQLAlchemyError:
                # If no dataType group present for current op. group, create it.
                operation_group = dao.get_operationgroup_by_id(operation_group_id)
                datatype_group = DataTypeGroup(operation_group, operation_id=operation_entity.id)
                datatype_group.state = UploadAlgorithmCategoryConfig.defaultdatastate
                datatype_group = dao.store_entity(datatype_group)

        return operation_entity, datatype_group

    def import_simulator_configuration_zip(self, zip_file):
        # Now compute the name of the folder where to explode uploaded ZIP file
        temp_folder = self._compute_unpack_path()
        uq_file_name = temp_folder + ".zip"

        if isinstance(zip_file, FieldStorage) or isinstance(zip_file, Part):
            if not zip_file.file:
                raise ServicesBaseException("Could not process the given ZIP file...")

            with open(uq_file_name, 'wb') as file_obj:
                self.files_helper.copy_file(zip_file.file, file_obj)
        else:
            shutil.copy2(zip_file, uq_file_name)

        try:
            self.files_helper.unpack_zip(uq_file_name, temp_folder)
            return temp_folder
        except FileStructureException as excep:
            raise ServicesBaseException("Could not process the given ZIP file..." + str(excep))
