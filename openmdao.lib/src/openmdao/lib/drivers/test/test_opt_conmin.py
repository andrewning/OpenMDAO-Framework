"""
Test the CONMIN optimizer component
"""

import unittest
import numpy
import logging

# pylint: disable-msg=F0401,E0611
from openmdao.main import Assembly, Component, ArrayVariable, Float
from openmdao.main.variable import INPUT, OUTPUT
from openmdao.lib.drivers.conmindriver import CONMINdriver

class OptRosenSuzukiComponent(Component):
    """ From the CONMIN User's Manual:
    EXAMPLE 1 - CONSTRAINED ROSEN-SUZUKI FUNCTION. NO GRADIENT INFORMATION.
    
         MINIMIZE OBJ = X(1)**2 - 5*X(1) + X(2)**2 - 5*X(2) +
                        2*X(3)**2 - 21*X(3) + X(4)**2 + 7*X(4) + 50
    
         Subject to:
    
              G(1) = X(1)**2 + X(1) + X(2)**2 - X(2) +
                     X(3)**2 + X(3) + X(4)**2 - X(4) - 8   .LE.0
    
              G(2) = X(1)**2 - X(1) + 2*X(2)**2 + X(3)**2 +
                     2*X(4)**2 - X(4) - 10                  .LE.0
    
              G(3) = 2*X(1)**2 + 2*X(1) + X(2)**2 - X(2) +
                     X(3)**2 - X(4) - 5                     .LE.0
                     
    This problem is solved beginning with an initial X-vector of
         X = (1.0, 1.0, 1.0, 1.0)
    The optimum design is known to be
         OBJ = 6.000
    and the corresponding X-vector is
         X = (0.0, 1.0, 2.0, -1.0)
    """
    
    # pylint: disable-msg=C0103
    def __init__(self, name, parent=None, doc=None):
        super(OptRosenSuzukiComponent, self).__init__(name, parent, doc)
        self.x = numpy.array([1., 1., 1., 1.], dtype=float)
        self.result = 0.
        ArrayVariable('x', self, iostatus=INPUT, entry_type=float)
        Float('result', self, iostatus=OUTPUT)
        
        self.opt_objective = 6.
        self.opt_design_vars = [0., 1., 2., -1.]

    def execute(self):
        """calculate the new objective value"""
        self.result = (self.x[0]**2 - 5.*self.x[0] + 
                       self.x[1]**2 - 5.*self.x[1] +
                       2.*self.x[2]**2 - 21.*self.x[2] + 
                       self.x[3]**2 + 7.*self.x[3] + 50)


class CONMINdriverTestCase(unittest.TestCase):
    """test CONMIN optimizer component"""

    def setUp(self):
        self.top = Assembly('top', None)
        self.top.add_child(OptRosenSuzukiComponent('comp', self.top))
        self.top.add_child(CONMINdriver('driver', self.top))
        self.top.driver.iprint = 0
        self.top.driver.maxiters = 30
        
    def tearDown(self):
        self.top = None
        
    def test_opt1(self):
        self.top.driver.objective.value = 'comp.result'
        self.top.driver.design_vars.value = ['comp.x[0]', 'comp.x[1]',
                                             'comp.x[2]', 'comp.x[3]']
        self.top.driver.lower_bounds = [-10, -10, -10, -10]
        self.top.driver.upper_bounds = [99, 99, 99, 99]
        
        # pylint: disable-msg=C0301
        self.top.driver.constraints.value = [
            'comp.x[0]**2+comp.x[0]+comp.x[1]**2-comp.x[1]+comp.x[2]**2+comp.x[2]+comp.x[3]**2-comp.x[3]-8',
            'comp.x[0]**2-comp.x[0]+2*comp.x[1]**2+comp.x[2]**2+2*comp.x[3]**2-comp.x[3]-10',
            '2*comp.x[0]**2+2*comp.x[0]+comp.x[1]**2-comp.x[1]+comp.x[2]**2-comp.x[3]-5']        
        self.top.run()
        # pylint: disable-msg=E1101
        self.assertAlmostEqual(self.top.comp.opt_objective, 
                               self.top.driver.objective.refvalue, places=2)
        self.assertAlmostEqual(self.top.comp.opt_design_vars[0], 
                               self.top.comp.x[0], places=1)
        self.assertAlmostEqual(self.top.comp.opt_design_vars[1], 
                               self.top.comp.x[1], places=2)
        self.assertAlmostEqual(self.top.comp.opt_design_vars[2], 
                               self.top.comp.x[2], places=2)
        self.assertAlmostEqual(self.top.comp.opt_design_vars[3], 
                               self.top.comp.x[3], places=1)

        
    def test_bad_objective(self):
        try:
            self.top.driver.objective.value = 'comp.missing'
        except RuntimeError, err:
            self.assertEqual(str(err), "top.driver.objective: cannot find variable 'comp.missing'")
        else:
            self.fail('RuntimeError expected')


    def test_no_design_vars(self):
        self.top.driver.objective.value = 'comp.result'
        try:
            self.top.run()
        except RuntimeError, err:
            self.assertEqual(str(err), "top.driver: no design variables specified")
        else:
            self.fail('RuntimeError expected')
    
    def test_no_objective(self):
        self.top.driver.design_vars.value = ['comp.x[0]', 'comp.x[1]',
                                             'comp.x[2]', 'comp.x[3]']
        try:
            self.top.run()
        except RuntimeError, err:
            self.assertEqual(str(err), "top.driver.objective: reference is undefined")
        else:
            self.fail('RuntimeError expected')
            
    def test_get_objective(self):
        self.top.driver.objective.value = 'comp.result'
        self.assertEqual('comp.result', self.top.driver.objective.value)
    
    def test_update_objective(self):
        try:
            val = self.top.driver.objective.refvalue
        except RuntimeError, err:
            self.assertEqual(str(err), "top.driver.objective: reference is undefined")
        else:
            self.fail('RuntimeError expected')
        self.top.comp.result = 99.
        self.top.driver.objective.value = 'comp.result'
        val = self.top.driver.objective.refvalue
        self.assertEqual(val, 99.)
    
    def test_bad_design_vars(self):
        try:
            self.top.driver.design_vars.value = ['comp_bogus.x[0]', 'comp.x[1]']
        except RuntimeError, err:
            self.assertEqual(str(err), 
                    "top.driver.design_vars: cannot find variable 'comp_bogus.x'")
        else:
            self.fail('RuntimeError expected')
    
    def test_bad_constraint(self):
        try:
            self.top.driver.constraints.value = ['bogus.flimflam']
        except RuntimeError, err:
            self.assertEqual(str(err), 
                 "top.driver.constraints: cannot find variable 'bogus.flimflam'")
        else:
            self.fail('RuntimeError expected')
            
    def test_lower_bounds_mismatch(self):
        self.top.driver.objective.value = 'comp.result'
        self.top.driver.design_vars.value = ['comp.x[0]', 'comp.x[1]']
        self.top.driver.lower_bounds = [0, 0, 0, 0]
        try:
            self.top.run()
        except ValueError, err:
            self.assertEqual(str(err),
                             "top.driver: size of new lower bound array"+
                             " (4) does not match number of design vars (2)")
        else:
            self.fail('ValueError expected')
            
    def test_upper_bounds_mismatch(self):
        self.top.driver.objective.value = 'comp.result'
        self.top.driver.design_vars.value = ['comp.x[0]', 'comp.x[1]']
        self.top.driver.upper_bounds = [99]
        try:
            self.top.run()
        except ValueError, err:
            self.assertEqual(str(err),
                             "top.driver: size of new upper bound array"+
                             " (1) does not match number of design vars (2)")
        else:
            self.fail('ValueError expected')

    
if __name__ == "__main__":
    unittest.main()
    #suite = unittest.TestLoader().loadTestsFromTestCase(ContainerTestCase)
    #unittest.TextTestRunner(verbosity=2).run(suite)    

