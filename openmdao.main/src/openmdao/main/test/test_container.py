# pylint: disable-msg=C0111,C0103

import unittest
import StringIO


import openmdao.main.constants as constants
from openmdao.main import Container, Float
from openmdao.main.interfaces import IContainer
from openmdao.main.variable import INPUT



class ContainerTestCase(unittest.TestCase):

    def setUp(self):
        """This sets up the following hierarchy of Containers:
        
                       root
                       /  \
                     c1    c2
                          /  \
                        c21  c22
                             /
                          c221
                          /
                        number
        """
        
        self.root = Container('root', None)
        c1 = Container('c1', None)
        c2 = Container('c2', None)
        self.root.add_child(c1)
        self.root.add_child(c2)        
        c21 = Container('c21', None)
        c22 = Container('c22', None)
        c2.add_child(c21)
        c2.add_child(c22)
        c221 = Container('c221', None)
        c221.number = 3.14
        c22.add_child(c221)
        ff = Float('number', c221, INPUT)
        ff.units = "ft/s"

    def tearDown(self):
        """this teardown function will be called after each test"""
        self.root = None

    def test_add_child(self):
        foo = Container('foo', None)
        non_container = 'some string'
        try:
            foo.add_child(non_container)
        except TypeError, err:
            self.assertEqual(str(err), "foo: '<type 'str'>' "+
                "object has does not provide the IContainer interface")
        else:
            self.fail('TypeError expected')
        
    def test_pathname(self):
        foo = Container('foo', None)
        self.root.add_child(foo)
        self.assertEqual(foo.get_pathname(), 'root.foo')


    def test_get(self):
        obj = self.root.get('c2.c21')
        self.assertEqual(obj.get_pathname(), 'root.c2.c21')
        num = self.root.get('c2.c22.c221.number')
        self.assertEqual(num, 3.14)
        num = self.root.get('c2.c22.c221.number.value')
        self.assertEqual(num, 3.14)

    def test_get_attribute(self):
        units = self.root.get('c2.c22.c221.number.units')
        self.assertEqual(units, "ft/s")

    def test_keys(self):
        lst = [x for x in self.root.keys(recurse=True)]
        self.assertEqual(lst, 
            ['c2', 'c2.c22', 'c2.c22.c221', 'c2.c22.c221.number', 'c2.c21', 'c1'])
        
    def test_pub_items(self):
        lst = map(lambda x: x[0], self.root.items(recurse=True))
        self.assertEqual(lst, 
            ['c2', 'c2.c22', 'c2.c22.c221', 'c2.c22.c221.number', 'c2.c21', 'c1'])
        
    def test_full_items(self):
        lst = map(lambda x: x[0], self.root.items(pub=False,recurse=True))
        self.assertEqual(lst, ['name', 'c2', 'c2.c22', 'c2.c22.name', 
                               'c2.c22.c221', 'c2.c22.c221.name', 'c2.c22.c221.number', 
                               'c2.c21', 'c2.c21.name',
                               'c2.name', 'c1', 'c1.name'])
        
        items = [(x[0],isinstance(x[1],Container) or str(x[1])) 
                    for x in self.root.items(pub=False,recurse=True)]
        
        # values of True in the list below just indicate that the value
        # is a Container
        self.assertEqual(items, [('name', 'root'), ('c2', True), 
                                 ('c2.c22', True), ('c2.c22.name', 'c22'),
                                 ('c2.c22.c221', True), 
                                 ('c2.c22.c221.name', 'c221'), 
                                 ('c2.c22.c221.number', '3.14'), 
                                 ('c2.c21', True), 
                                 ('c2.c21.name', 'c21'), ('c2.name', 'c2'), 
                                 ('c1', True), ('c1.name', 'c1')])
        
    def test_bad_get(self):
        try:
            self.root.get('bogus')
        except AttributeError, err:
            self.assertEqual(str(err),"root: object has no attribute 'bogus'")
        else:
            self.fail('AttributeError expected')

    def test_bad_set(self):
        try:
            self.root.set('bogus', 99)
        except AttributeError, err:
            self.assertEqual(str(err),"root: object has no attribute 'bogus'")
        else:
            self.fail('AttributeError expected')

    def test_bad_getvar(self):
        try:
            self.root.getvar('bogus')
        except AttributeError, err:
            self.assertEqual(str(err),"root: object has no attribute 'bogus'")
        else:
            self.fail('AttributeError expected')

    def test_bad_setvar(self):
        try:
            self.root.setvar('bogus', 99)
        except AttributeError, err:
            self.assertEqual(str(err),"root: object has no attribute 'bogus'")
        else:
            self.fail('AttributeError expected')

    def test_iteration(self):
        names = [x.get_pathname() for x in self.root.values(pub=False,recurse=True)
                                         if IContainer.providedBy(x)]
        self.assertEqual(sorted(names),
                         ['root.c1', 'root.c2', 'root.c2.c21', 
                          'root.c2.c22', 'root.c2.c22.c221'])
        
        names = [x.get_pathname() for x in self.root.values(pub=False)
                                         if IContainer.providedBy(x)]
        self.assertEqual(sorted(names), ['root.c1', 'root.c2'])
        
        names = [x.get_pathname() for x in self.root.values(pub=False,recurse=True)
                                 if IContainer.providedBy(x) and x.parent==self.root]
        self.assertEqual(sorted(names), ['root.c1', 'root.c2'])        

        names = [x.get_pathname() for x in self.root.values(pub=False,recurse=True)
                                 if IContainer.providedBy(x) and x.parent==self.root.c2]
        self.assertEqual(sorted(names), ['root.c2.c21', 'root.c2.c22'])        

    def test_create(self):
        new_obj = self.root.create('openmdao.main.component.Component','mycomp')
        self.assertEqual(new_obj.__class__.__name__, 'Component')
        new_obj.run()
 
    # TODO: all of these save/load test functions need to do more checking
    #       to verify that the loaded thing is equivalent to the saved thing
    
    def test_save_load_yaml(self):
        output = StringIO.StringIO()
        c1 = Container('c1', None)
        c2 = Container('c2', None)
        c1.add_child(c2)
        c1.save(output, constants.SAVE_YAML)
        
        inp = StringIO.StringIO(output.getvalue())
        newc1 = Container.load(inp, constants.SAVE_YAML)
                
    def test_save_load_libyaml(self):
        output = StringIO.StringIO()
        c1 = Container('c1', None)
        c2 = Container('c2', None)
        c1.add_child(c2)
        c1.save(output, constants.SAVE_LIBYAML)
        
        inp = StringIO.StringIO(output.getvalue())
        newc1 = Container.load(inp, constants.SAVE_LIBYAML)
                
    def test_save_load_cpickle(self):
        output = StringIO.StringIO()
        c1 = Container('c1', None)
        c2 = Container('c2', None)
        c1.add_child(c2)
        c1.save(output)
        
        inp = StringIO.StringIO(output.getvalue())
        newc1 = Container.load(inp)
        
    def test_save_load_pickle(self):
        output = StringIO.StringIO()
        c1 = Container('c1', None)
        c2 = Container('c2', None)
        c1.add_child(c2)
        c1.save(output, constants.SAVE_PICKLE)
        
        inp = StringIO.StringIO(output.getvalue())
        newc1 = Container.load(inp, constants.SAVE_PICKLE)
                

if __name__ == "__main__":
    unittest.main()
