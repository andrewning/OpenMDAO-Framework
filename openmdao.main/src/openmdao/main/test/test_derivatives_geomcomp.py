"""
Testing differentiation of user-defined datatypes.
"""

import unittest

import numpy as np

from openmdao.lib.components.geomcomp import GeomComponent
from openmdao.lib.geometry.box import BoxParametricGeometry
from openmdao.main.api import Component, Assembly, set_as_top
from openmdao.main.datatypes.api import Float, Array
from openmdao.main.interfaces import IParametricGeometry, implements, \
                                     IStaticGeometry
from openmdao.main.variable import Variable
from openmdao.util.testutil import assert_rel_error

from openmdao.main.interfaces import IParametricGeometry, implements, IStaticGeometry


#not a working geometry, but pretends to be! only useful for this test
class DummyGeometry(object): 
    implements(IParametricGeometry, IStaticGeometry)

    def __init__(self): 
        self.vars = {'x':np.array([1,2]), 'y':1, 'z':np.array([0,0])}
        self._callbacks = []


    def list_parameters(self): 
        self.params = []
        meta = {'value':np.array([1,2]), 'iotype':'in', 'shape':(2,)}
        self.params.append(('x', meta))

        meta = {'value':1.0, 'iotype':'in',}
        self.params.append(('y', meta))

        meta = {'value':np.array([0,0]), 'iotype':'out', 'shape':(2,)}
        self.params.append(('z', meta))

        meta = {'iotype':'out', 'data_shape':(2,), 'type':IStaticGeometry}
        self.params.append(('geom_out',meta))

        return self.params

    def set_parameter(self, name, val): 
        self.vars[name] = val

    def get_parameters(self, names): 
        return [self.vars[n] for n in names]

    def linearize(self): 
        self.J = np.array([[2, 0, 1],
                          [0, 2, 1]])
        self.JT = self.J.T

    def apply_deriv(self, arg, result): 
        if 'x' in arg: 
            if 'z' in result: 
                result['z'] += self.J[:,:1].dot(arg['x'])
            if 'geom_out' in result:
                result['geom_out'] += self.J[:,:1].dot(arg['x'])
        if 'y' in arg: 
            if 'z' in result: 
                result['z'] += self.J[:,2].dot(arg['y'])
            if 'geom_out' in result:
                result['geom_out'] += self.J[:,2].dot(arg['y'])

        return result


    def apply_derivT(self, arg, result): 
        if 'z' in arg: 
            if 'x' in 'result':
                result['x'] += self.JT[:1,:].dot(arg['z'])
            if 'y' in 'result': 
                result['y'] += self.JT[2,:].dot(arg['z'])
        if 'geom_out' in arg: 
            if 'x' in 'result':
                result['x'] += self.JT[:1,:].dot(arg['geom_out'])
            if 'y' in 'result': 
                result['y'] += self.JT[2,:].dot(arg['geom_out'])


        return result


    def regen_model(self):
        x = self.vars['x']
        y = self.vars['y']

        self.z = 2*x + y 
        self.vars['z'] = self.z

    def get_static_geometry(self): 
        return self

    def register_param_list_changedCB(self, callback):
        self._callbacks.append(callback)

    def _invoke_callbacks(self): 
        for cb in self._callbacks: 
            cb()

    def get_visualization_data(self, wv): #stub
        pass


class GeomRecieve(Component): 

    geom_in = Variable(DummyGeometry(), iotype='in', data_shape=(2, ))
    out = Array([0,0], iotype="out")

    def execute(self): 
        self.out = self.geom_in.z

    
class Testcase_deriv_obj(unittest.TestCase):
    """ Test run/step/stop aspects of a simple workflow. """

    
    def test_geom_provide_deriv_check(self):    
        
        top = set_as_top(Assembly())
        top.add('c1', GeomComponent())
        top.c1.add('parametric_geometry', DummyGeometry())
        top.add('c2', GeomRecieve())
        top.connect('c1.geom_out', 'c2.geom_in')
        top.driver.workflow.add(['c1', 'c2'])
        
        top.c1.x = [3.0,4.0]
        top.c1.y = 10
        top.run()
        
        inputs = ['c1.x', 'c1.y']
        outputs = ['c1.z','c2.out']
        J = top.driver.workflow.calc_gradient(inputs, outputs, fd=True)

        print J
        
        #assert_rel_error(self, J[0, 0], -6.0, .00001)
        #assert_rel_error(self, J[0, 1], -1.0, .00001)
        #assert_rel_error(self, J[1, 0], -2.0, .00001)
        #assert_rel_error(self, J[1, 1], 20.0, .00001)
        #assert_rel_error(self, J[2, 0], 6.0, .00001)
        #assert_rel_error(self, J[2, 1], 9.0, .00001)
        
        top.driver.workflow.config_changed()
        J = top.driver.workflow.calc_gradient(inputs, outputs, mode='forward')

        print J
        
        #assert_rel_error(self, J[0, 0], -6.0, .00001)
        #assert_rel_error(self, J[0, 1], -1.0, .00001)
        #assert_rel_error(self, J[1, 0], -2.0, .00001)
        #assert_rel_error(self, J[1, 1], 20.0, .00001)
        #assert_rel_error(self, J[2, 0], 6.0, .00001)
        #assert_rel_error(self, J[2, 1], 9.0, .00001)
        
        top.driver.workflow.config_changed()
        J = top.driver.workflow.calc_gradient(inputs, outputs, mode='adjoint')

        print J
        
        #assert_rel_error(self, J[0, 0], -6.0, .00001)
        #assert_rel_error(self, J[0, 1], -1.0, .00001)
        #assert_rel_error(self, J[1, 0], -2.0, .00001)
        #assert_rel_error(self, J[1, 1], 20.0, .00001)
        #assert_rel_error(self, J[2, 0], 6.0, .00001)
        #assert_rel_error(self, J[2, 1], 9.0, .00001)


    
        
if __name__ == '__main__':
    import nose
    import sys
    sys.argv.append('--cover-package=openmdao')
    sys.argv.append('--cover-erase')
    nose.runmodule()
