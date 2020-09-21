# -*- coding: utf-8 -*-
import os
import abc
import numpy
from tvb.basic.neotraits.api import HasTraits, Attr, Float, NArray, List
from tvb.simulator.common import iround
from tvb.simulator.history import BaseHistory, SparseHistory


class CosimUpdate(HasTraits):

    proxy_inds = NArray(
        dtype=numpy.int,
        label="Indices of TVB proxy nodes",
        required=True,
    )


class CosimStateUpdate(CosimUpdate):

    voi = NArray(
        dtype=int,
        label="Cosimulation model state variables' indices",
        doc=("Indices of model's variables of interest (VOI) that"
             "should be updated (i.e., overwriten) during cosimulation."),
        required=False)

    exclusive = Attr(
        field_type=bool,
        default=False, required=False,
        doc="1, when the proxy nodes substitute TVB nodes and their mutual connections should be removed.")

    def configure(self, simulator):
        if self.voi is None or self.voi.size == 0:
            self.voi = numpy.r_[:len(simulator.model.variables_of_interest)]

    def _update(self, data):
        return data

    def update(self, state, update=None):
        state[self.voi, self.proxy_inds] = self._update(update)
        return state


class CosimStateUpdateFromFile(CosimStateUpdate):

    path = Attr(field_type=os.PathLike, required=False, default="")

    def _update(self, data=None):
        raise NotImplementedError


class CosimStateUpdateFromMPI(CosimStateUpdate):

    path = Attr(field_type=os.PathLike, required=False, default="")

    def _update(self, data=None):
        raise NotImplementedError


class CosimHistoryUpdate(CosimUpdate):

    history = Attr(
        field_type=BaseHistory,
        label="Simulator history",
        default=SparseHistory(),
        required=True,
        doc="""A tvb.simulator.history""")

    voi = NArray(
        dtype=int,
        label="Cosimulation model coupling variables",
        doc=("Indices of model's coupling variables that "
             "should be updated (i.e., overwriten) during cosimulation. "
             "Note that the indices should start at zero, so that if a model offers VOIs V, W and "
             "V+W, and W is selected, and this monitor should record W, then the correct index is 0."),
        required=False)

    period = Float(
        label="Updating period (ms)",
        required=False,
        default=0.9765625,  # ms. 0.9765625 => 1024Hz #ms, 0.5 => 2000Hz
        doc="""Updating period in milliseconds, must be an integral multiple
                    of integration-step size. As a guide: 2048 Hz => 0.48828125 ms ;  
                    1024 Hz => 0.9765625 ms ; 512 Hz => 1.953125 ms.""")

    dt = Float(
        label="Integration step (ms)",  # order = 10
        default=0.9765625,  # ms. 0.9765625 => 1024Hz #ms, 0.5 => 2000Hz
        required=False,
        doc="""Sampling period in milliseconds, must be an integral multiple
                    of integration-step size. As a guide: 2048 Hz => 0.48828125 ms ;  
                    1024 Hz => 0.9765625 ms ; 512 Hz => 1.953125 ms.""")

    def configure(self, simulator):
        self.history = simulator.history
        if self.voi is None or self.voi.size == 0:
            self.voi = numpy.r_[:len(simulator.model.cvar)]
        self.configure_input_update()
        self.dt = simulator.integrator.dt
        self.istep = iround(self.period / self.dt)
        if self.istep > self.history.n_time:
            raise ValueError("Synchronization time %g for cosimulation update cannot "
                             "be longer than the history buffer time length %g!"
                             % (self.period, self.dt * self.n_time))

    def update(self, step, update=None):
        if step % self.istep == 0:
            start_time_idx = (step - self.istep + 1) % self.n_time
            end_time_idx = step % self.n_time + 1
            self.history.buffer[start_time_idx:end_time_idx,
                                self.voi,
                                self.proxy_inds] = self._update(update)


class CosimHistoryUpdateFromFile(CosimHistoryUpdate):

    path = Attr(field_type=os.PathLike, required=False, default="")

    def _update(self, data=None):
        raise NotImplementedError


class CosimHistoryUpdateFromMPI(CosimHistoryUpdate):

    path = Attr(field_type=os.PathLike, required=False, default="")

    def _update(self, data=None):
        raise NotImplementedError


class CosimToTVBInterfaces(HasTraits):

    state_interfaces = List(of=CosimStateUpdate)
    history_interfaces = List(of=CosimHistoryUpdate)
