from tvb.simulator.models.base import Model, ModelNumbaDfun
import numexpr
import numpy
from numpy import *
from numba import guvectorize, float64
from tvb.basic.neotraits.api import NArray, Final, List, Range

class oscillator(ModelNumbaDfun):
        
    tau = NArray(
        label=":math:`tau`",
        default=numpy.array([1.0]),
        doc="""A time-scale hierarchy can be introduced for the state variables :math:`V` and :math:`W`. Default parameter is 1, which means no time-scale hierarchy."""
    )    
        
    I = NArray(
        label=":math:`I`",
        default=numpy.array([0.0]),
        doc="""Baseline shift of the cubic nullcline"""
    )    
        
    a = NArray(
        label=":math:`a`",
        default=numpy.array([-2.0]),
        doc="""Vertical shift of the configurable nullcline"""
    )    
        
    b = NArray(
        label=":math:`b`",
        default=numpy.array([-10.0]),
        doc="""Linear slope of the configurable nullcline"""
    )    
        
    c = NArray(
        label=":math:`c`",
        default=numpy.array([0]),
        doc="""Parabolic term of the configurable nullcline"""
    )    
        
    d = NArray(
        label=":math:`d`",
        default=numpy.array([0.02]),
        doc="""Temporal scale factor. Warning: do not use it unless you know what you are doing and know about time tides."""
    )    
        
    e = NArray(
        label=":math:`e`",
        default=numpy.array([3.0]),
        doc="""Coefficient of the quadratic term of the cubic nullcline."""
    )    
        
    f = NArray(
        label=":math:`f`",
        default=numpy.array([1.0]),
        doc="""Coefficient of the cubic term of the cubic nullcline."""
    )    
        
    g = NArray(
        label=":math:`g`",
        default=numpy.array([0.0]),
        doc="""Coefficient of the linear term of the cubic nullcline."""
    )    
        
    alpha = NArray(
        label=":math:`alpha`",
        default=numpy.array([1.0]),
        doc="""Constant parameter to scale the rate of feedback from the slow variable to the fast variable."""
    )    
        
    beta = NArray(
        label=":math:`beta`",
        default=numpy.array([1.0]),
        doc="""Constant parameter to scale the rate of feedback from the slow variable to itself"""
    )    
        
    gamma = NArray(
        label=":math:`gamma`",
        default=numpy.array([1.0]),
        doc="""Constant parameter to reproduce FHN dynamics where excitatory input currents are negative.             It scales both I and the long range coupling term.."""
    )    

    state_variable_range = Final(
        label="State Variable ranges [lo, hi]",
        default={"V": numpy.array([]), 
				 "W": numpy.array([])},
        doc="""state variables"""
    )

    state_variable_boundaries = Final(
        label="State Variable boundaries [lo, hi]",
        default={"V": numpy.array([-2.0, 4.0])"W": numpy.array([-6.0, 6.0])},
    )
    variables_of_interest = List(
        of=str,
        label="Variables or quantities available to Monitors",
        choices=('V', ),
        default=('V', 'W', ),
        doc="Variables to monitor"
    )

    state_variables = ['V', 'W']

    _nvar = 2
    cvar = numpy.array([0], dtype=numpy.int32)

    def dfun(self, vw, c, local_coupling=0.0):
        vw_ = vw.reshape(vw.shape[:-1]).T
        c_ = c.reshape(c.shape[:-1]).T
        deriv = _numba_dfun_oscillator(vw_, c_, self.tau, self.I, self.a, self.b, self.c, self.d, self.e, self.f, self.g, self.alpha, self.beta, self.gamma, local_coupling)

        return deriv.T[..., numpy.newaxis]

@guvectorize([(float64[:], float64[:], float64, float64, float64, float64, float64, float64, float64, float64, float64, float64, float64, float64, float64, float64[:])], '(n),(m)' + ',()'*13 + '->(n)', nopython=True)
def _numba_dfun_oscillator(vw, coupling, tau, I, a, b, c, d, e, f, g, alpha, beta, gamma, local_coupling, dx):
    "Gufunc for oscillator model equations."

    V = vw[0]
    W = vw[1]




    dx[0] = d * tau * (alpha * W - f * powf(V, 3) + e * powf(V, 2) + g * V + gamma * I + gamma * c_0 + lc * V)
    dx[1] = d * (a + b * V + c * powf(V, 2) - beta * W) / tau
            