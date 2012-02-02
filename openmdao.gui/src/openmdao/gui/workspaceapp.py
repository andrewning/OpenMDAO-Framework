import os, sys
import os.path

from optparse import OptionParser

# zmq
import zmq
from zmq.eventloop import ioloop, zmqstream
ioloop.install()

# tornado
from tornado import httpserver, ioloop, web

# openmdao
from openmdao.util.network import get_unused_ip_port
from openmdao.gui.util import ensure_dir, launch_browser
from openmdao.gui.consoleserverfactory import ConsoleServerFactory

import openmdao.gui.projdb_handlers    as proj
import openmdao.gui.workspace_handlers as wksp


class BaseHandler(web.RequestHandler):
    ''' override the get_current_user() method in your request handlers to determine
        the current user based on the value of a cookie.
    '''
    def get_current_user(self):
        return self.get_secure_cookie("user")

class LoginHandler(BaseHandler):
    ''' lets users log into the application simply by specifying a nickname,
        which is then saved in a cookie.
    '''
    def get(self):
        self.write('<html><body bgcolor="Grey"><form action="/login" method="post">'
                   'Name: <input type="text" name="name">'
                   '<input type="submit" value="Sign in">'
                   '</form></body></html>')

    def post(self):
        self.set_secure_cookie("user", self.get_argument("name"))
        self.redirect("/")

class LogoutHandler(BaseHandler):
    ''' lets users log out of the application simply by deleting the nickname cookie
    '''
    def get(self):
        self.clear_cookie("user")
        self.redirect("/")

    def post(self):
        self.clear_cookie("user")
        self.redirect("/")

class WebApp(web.Application):
    ''' openmdao web application server
        extends tornado web app with URL mappings, settings and server manager
    '''

    def __init__(self, server_mgr):
        handlers = [
            web.url(r'/login',  LoginHandler),
            web.url(r'/logout', LogoutHandler),
            
            web.url(r'/',                                        proj.IndexHandler),
            web.url(r'/projects/?',                              proj.IndexHandler),
            web.url(r'/projects/(?P<project_id>\d+)/?',          proj.DetailHandler),
            web.url(r'/projects/new/$',                          proj.NewHandler),
            web.url(r'/projects/add/$',                          proj.AddHandler),
            web.url(r'/projects/delete/(?P<project_id>\d+)/?',   proj.DeleteHandler),
            web.url(r'/projects/download/(?P<project_id>\d+)/?', proj.DownloadHandler),
            
            web.url(r'/workspace/?',                wksp.WorkspaceHandler, name='workspace'),
            web.url(r'/workspace/components/?',     wksp.ComponentsHandler),
            web.url(r'/workspace/component/(.*)',   wksp.ComponentHandler),
            web.url(r'/workspace/connections/(.*)', wksp.ConnectionsHandler),
            web.url(r'/workspace/addons/?',         wksp.AddOnsHandler),
            web.url(r'/workspace/close/?',          wksp.CloseHandler),
            web.url(r'/workspace/command',          wksp.CommandHandler),
            web.url(r'/workspace/structure/(.*)/?', wksp.StructureHandler),
            web.url(r'/workspace/exec/?',           wksp.ExecHandler),
            web.url(r'/workspace/exit/?',           wksp.ExitHandler),
            web.url(r'/workspace/file/(.*)',        wksp.FileHandler),
            web.url(r'/workspace/files/?',          wksp.FilesHandler),
            web.url(r'/workspace/geometry',         wksp.GeometryHandler),
            web.url(r'/workspace/model/?',          wksp.ModelHandler),
            web.url(r'/workspace/output/?',         wksp.OutputHandler),
            web.url(r'/workspace/plot/?',           wksp.PlotHandler),
            web.url(r'/workspace/project/?',        wksp.ProjectHandler),
            web.url(r'/workspace/types/?',          wksp.TypesHandler),
            web.url(r'/workspace/upload/?',         wksp.UploadHandler),
            web.url(r'/workspace/workflow/(.*)',    wksp.WorkflowHandler),
            web.url(r'/workspace/test/?',           wksp.TestHandler),
        ]
        
        settings = { 
            'login_url':         '/login',
            'static_path':       os.path.join(os.path.dirname(__file__), 'static'),
            'template_path':     os.path.join(os.path.dirname(__file__), 'tmpl'),
            'cookie_secret':     os.urandom(1024),
            'debug':             True,
        }
        
        super(WebApp, self).__init__(handlers, **settings)
        
        self.server_mgr = server_mgr

class WorkspaceApp(object):
    ''' openmdao workspace application
        wraps tornado web app, runs http server and opens browser
    '''

    def __init__(self,options):
        self.options = options
        
        if options.initialize or not os.path.exists('settings.py'):
            if options.reset:
                initialize_settings(reset=True)
            else:
                initialize_settings(reset=False)

        if (options.port < 1):
            options.port = get_unused_ip_port()

        self.server_mgr = ConsoleServerFactory()
        self.web_app = WebApp(self.server_mgr)
        self.http_server = httpserver.HTTPServer(self.web_app)
        self.http_server.listen(options.port)
        
        if not options.serveronly:
            launch_browser(options.port, options.browser)

        ioloop.IOLoop.instance().start()

    @staticmethod
    def get_options_parser():
        ''' create a parser for command line arguments
        '''
        parser = OptionParser()
        parser.add_option("-p", "--port", type="int", dest="port", default=0,
                          help="port to run server on (defaults to any available port)")
        parser.add_option("-b", "--browser", dest="browser", default="chrome",
                          help="preferred browser")
        parser.add_option("-s", "--server", action="store_true", dest="serveronly",
                          help="don't launch browser, just run server")
        parser.add_option("-i", "--init", action="store_true", dest="initialize",
                          help="(re)initialize settings")
        parser.add_option("-r", "--reset", action="store_true", dest="reset",
                          help="reset project database (valid only with -i and without -d)")
        return parser

    def initialize_settings(reset):
        ''' first time setup (or re-setup)
        '''
        print "Initializing settings..."
        
        user_dir = os.path.expanduser("~/.openmdao/gui/")  # TODO: could put in a prefs file
        ensure_dir(user_dir)
        
        settings_file = "settings.py"
        database_file = user_dir+"mdaoproj.db"
        media_storage = user_dir+"media"
        
        if os.path.exists(settings_file):
            os.remove(settings_file)
        o = open(settings_file,"a") #open for append
        for line in open("settings.tmp"):
           line = line.replace("'NAME': 'mdaoproj.db'","'NAME': '"+database_file+"'")
           line = line.replace("MEDIA_ROOT = ''","MEDIA_ROOT = '"+media_storage+"'")
           o.write(line) 
        o.close()
        
        import settings
        print "MEDIA_ROOT=",settings.MEDIA_ROOT
        print "DATABASE=",settings.DATABASES['default']['NAME']
        
        print "Resetting project database..."
        if reset and os.path.exists(database_file):
            print "Deleting existing project database..."
            os.remove(database_file)
        from django.core.management import execute_manager
        execute_manager(settings,argv=[__file__,'syncdb'])


def main():
    ''' process command line arguments and do as commanded
    '''
    parser = WorkspaceApp.get_options_parser()
    (options, args) = parser.parse_args()
    app = WorkspaceApp(options)
    
if __name__ == '__main__':
    main()

