#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
logger = logging.getLogger(__name__)
logger.debug("%s loaded", __name__)

import os
from mimetypes import guess_type
from BaseHTTPServer import BaseHTTPRequestHandler
import cgi # for parsing POST
from urlparse import urlparse, parse_qs # parsing parameters and url
import re # regex for area
import json # for virtual resources
from urllib2 import urlopen as load_online_fallback

from action.base import SingleAction
import doorpi

VIRTUELL_RESOURCES = [
    '/mirror',
    '/status',
    '/control'
]

class WebServerLoginRequired(Exception): pass
class WebServerRequestHandlerShutdownAction(SingleAction): pass

class DoorPiWebRequestHandler(BaseHTTPRequestHandler):

    @property
    def conf(self): return self.server.config

    def log_error(self, format, *args): logger.error("[%s] %s", self.client_address[0], args)
    def log_message(self, format, *args): logger.debug("[%s] %s", self.client_address[0], args)

    @staticmethod
    def prepare():
        doorpi.DoorPi().event_handler.register_event('OnWebServerRequest', __name__)
        doorpi.DoorPi().event_handler.register_event('OnWebServerRequestGet', __name__)
        doorpi.DoorPi().event_handler.register_event('OnWebServerRequestPost', __name__)
        doorpi.DoorPi().event_handler.register_event('OnWebServerVirtualResource', __name__)
        doorpi.DoorPi().event_handler.register_event('OnWebServerRealResource', __name__)

    @staticmethod
    def destroy():
        doorpi.DoorPi().event_handler.unregister_source( __name__, True)

    def do_GET(self):
        if not self.server.keep_running: return

        parsed_path = urlparse(self.path)
        #doorpi.DoorPi().event_handler('OnWebServerRequest', __name__, {'header': self.headers.items(), 'path': parsed_path})
        #doorpi.DoorPi().event_handler('OnWebServerRequestGet', __name__, {'header': self.headers.items(), 'path': parsed_path})
        if parsed_path.path in VIRTUELL_RESOURCES:
            return self.create_virtual_resource(parsed_path, parse_qs(urlparse(self.path).query))
        else: return self.real_resource(parsed_path.path)

    def do_control(self):
        # http://192.168.178.43/control?module=keyboard.testsystem&name=11&value=1&output=json
        # http://192.168.178.43/control?module=event&name=OnWebserverStart&value=true&output=json
        # http://192.168.178.43/control?module=action&name=call&value=**613&output=json
        pass

    def create_virtual_resource(self, path, raw_parameters):
        #doorpi.DoorPi().event_handler('OnWebServerVirtualResource', __name__, {'path': path})

        if 'module' not in raw_parameters.keys(): raw_parameters['module'] = []
        if 'name' not in raw_parameters.keys(): raw_parameters['name'] = []
        if 'value' not in raw_parameters.keys(): raw_parameters['value'] = []
        if 'output' not in raw_parameters.keys(): raw_parameters['output'] = ''

        #for parameter in parameters.keys():
        #    try: parameters[parameter] = raw_parameters[parameter][0]
        #    except: pass

        return_object = {}
        try:
            if path.path == '/mirror':
                return_object = self.create_mirror()
                raw_parameters['output'] = "string"
            elif path.path == '/status':
                return_object = doorpi.DoorPi().get_status(
                    modules = raw_parameters['module'],
                    name = raw_parameters['name'],
                    value = raw_parameters['value']
                ).dictionary
        except Exception as return_object: pass

        return self.return_virtual_resource(return_object, raw_parameters['output'])

    def return_virtual_resource(self, prepared_object, return_type = 'json'):
        if isinstance(return_type, list) and len(return_type) > 0: return_type = return_type[0]

        if return_type in ["json", "default"]:
            return  self.return_message(json.dumps(prepared_object), "application/json; charset=utf-8")
        elif return_type in ["json_beautified", "json.beautified", "beautified.json", ""]:
            return  self.return_message(json.dumps(prepared_object, sort_keys=True, indent=4), "application/json; charset=utf-8")
        elif return_type in ["string", "plain", "str"]:
            return self.return_message(str(prepared_object))
        elif return_type in ["repr"]:
            return self.return_message(repr(prepared_object))
        else:
            try:    return self.return_message(repr(prepared_object))
            except: return self.return_message(str(prepared_object))
        pass

    def real_resource(self, path):
        #doorpi.DoorPi().event_handler('OnWebServerRealResource', __name__, {'path': path})
        if os.path.isdir(self.server.www + path): return self.list_directory(self.server.www + path)
        try:
            return self.return_file_content(path)
        except IOError as exp:
            return self.real_resource_fallback(path, exp)
        except Exception as exp:
            logger.exception(exp)
            return self.send_error(500, str(exp))

    def real_resource_fallback(self, path, previous_exception):
        try:
            return self.return_fallback_content(self.server.online_fallback, path)
        except IOError:
            return self.send_error(404, str(previous_exception))
        except Exception as exp:
            logger.exception(exp)
            return self.send_error(500, str(exp))

    def list_directory(self, path):
        dirs = []
        files = []
        for item in os.listdir(path):
            if os.path.isfile(item): files.append(item)
            else: dirs.append(item)

        return_html = '''<!DOCTYPE html>
            <html lang="en"><head></head><body><a href="..">..</a></br>
        '''
        for dir in dirs: return_html += '<a href="./{dir}">{dir}</a></br>'.format(dir = dir)
        for file in files: return_html += '<a href="./{file}">{file}</a></br>'.format(file = file)
        return_html += "</body></html>"
        return self.return_message(return_html, 'text/html')
        #return self.send_error(403)

    def return_redirection(self, new_location):
        message = '''
        <html>
        <meta http-equiv="refresh" content="0;url={new_location}">
        <a href="{new_location}">{new_location}</a>
        </html>
        '''.format(new_location = new_location)
        self.return_message(message, http_code = 301)
        self.wfile.write(message)

    def return_error(self, error_code = 404, error_message = None):
        self.send_error(error_code, error_message)
        raise Exception('sending error '+str(error_code)+' - '+str(error_message))

    @staticmethod
    def get_mime_typ(url):
        return guess_type(url)[0] or ""

    @staticmethod
    def is_file_parsable(filename):
        mime_type = DoorPiWebRequestHandler.get_mime_typ(filename)
        return mime_type in ['text/html']

    def read_from_file(self, url):
        try:
            read_mode = "r" if self.is_file_parsable(url) else "rb"
            with open(url, read_mode) as file:
                file_content = file.read()
            if self.is_file_parsable(url):
                return self.parse_content(file_content)
            else:
                return self.parse_content(file_content)
        except Exception as exp:
            raise exp

    def read_from_fallback(self, url):
        response = load_online_fallback(url, timeout = 1)
        if self.is_file_parsable(url):
            return self.parse_content(response.read(), True)
        else:
            return response.read()

    def return_file_content(self, path):
        content = mime = ""
        try:
            content = self.read_from_file(self.server.www + path)
            mime = self.get_mime_typ(self.server.www + path)
        except Exception as first_exp:
            try:
                content = self.read_from_fallback(self.server.online_fallback + path)
                mime = self.get_mime_typ(self.server.online_fallback + path)
            except Exception as exp:
                self.return_error(500, str(first_exp)+" - "+str(exp))

        return self.return_message(
            content,
            mime
        )

    def return_message(self, message = "", content_type = 'text/plain; charset=utf-8', http_code = 200, login_form = False):
        try:
            if not login_form: self.check_authentication()
        except WebServerLoginRequired: return self.login_form()

        self.send_response(http_code)
        if login_form:
            self.send_header('WWW-Authenticate', 'Basic realm=\"%s\"' % doorpi.DoorPi().name_and_version)
        self.send_header("Server", doorpi.DoorPi().name_and_version)
        self.send_header("Content-type", content_type)
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(message)

    def login_form(self):
        return_value = False
        try:
            login_form_content = '''
                <head>
                <title>Error response</title>
                </head>
                <body>
                <h1>Error response</h1>
                <p>Error code 401.
                <p>Message: <a href="http://tools.ietf.org/html/rfc7235#section-3.1">RFC7235 - 401 Unauthorized</a>
                <p>Error code explanation: 401 = Unauthorized.
                </body>
            '''
            login_form_content = self.read_from_file(self.server.www + self.server.loginfile)
            self.return_message(
                message = login_form_content ,
                content_type = 'text/html; charset=utf-8',
                http_code = 401,
                login_form = True
            )
            return_value = True
        except IOError as exp:   self.send_error(404, str(exp))
        except Exception as exp:
            logger.exception(exp)
            self.send_error(500, str(exp))
        return True if return_value else False

    def authentication_required(self):
        parsed_path = urlparse(self.path)

        for public_resource in self.conf.get_keys(self.server.area_public_name, log = False):
            if re.match(public_resource, parsed_path.path):
                logger.debug('public resource: %s',parsed_path.path)
                return False

        try:
            username, password = self.headers['authorization'].replace('Basic ', '').decode('base64').split(':', 1)
        except Exception as exp:
            logger.debug('no header Authorization object (%s)', exp)
            return True

        user_session = self.server.sessions.get_session(username)
        if not user_session:
            user_session = self.server.sessions.build_security_object(username, password)

        if not user_session:
            logger.debug('need authentication (no session): %s', parsed_path.path)
            return True

        for write_permission in user_session['writepermissions']:
            if re.match(write_permission, parsed_path.path):
                logger.info('user %s has write permissions: %s', user_session['username'], parsed_path.path)
                return False

        for read_permission in user_session['readpermissions']:
            if re.match(read_permission, parsed_path.path):
                logger.info('user %s has read permissions: %s', user_session['username'], parsed_path.path)
                return False

        logger.warning('user %s has no permissions: %s', user_session['username'], parsed_path.path)
        return True

    def check_authentication(self):
        if not self.authentication_required(): return True
        raise WebServerLoginRequired()

    def create_mirror(self):
        parsed_path = urlparse(self.path)
        message_parts = [
                'CLIENT VALUES:',
                'client_address=%s (%s)' % (self.client_address,
                                            self.address_string()),
                'raw_requestline=%s' % self.raw_requestline,
                'command=%s' % self.command,
                'path=%s' % self.path,
                'real path=%s' % parsed_path.path,
                'query=%s' % parsed_path.query,
                'request_version=%s' % self.request_version,
                '',
                'SERVER VALUES:',
                'server_version=%s' % self.server_version,
                'sys_version=%s' % self.sys_version,
                'protocol_version=%s' % self.protocol_version,
                '',
                'HEADERS RECEIVED:',
        ]
        for name, value in sorted(self.headers.items()):
            message_parts.append('%s=%s' % (name, value.rstrip()))
        message_parts.append('')
        message = '\r\n'.join(message_parts)
        return message

    def parse_content(self, content, online_fallback = False):
        try:
            matches = re.findall(r"{([^}\s]*)}", content)
            if not matches: return content
            #http://stackoverflow.com/questions/12897374/get-unique-values-from-a-list-in-python/12897491#12897491
            matches = list(set(matches))

            mapping_table = {
                'DOORPI': doorpi.DoorPi().name_and_version,
                'SERVER': self.server.server_name,
                'PORT': str(self.server.server_port),
                #nutze den Hostnamen aus der URL. sonst ist ein erneuter Login nötig
                'BASE_URL': "http://%s:%s"%(self.headers['host'], self.server.server_port)
                            if 'host' in self.headers.keys() else
                            "http://%s:%s"%(self.server.server_name, self.server.server_port)
            }
            # Trennung DATA_URL (AJAX) und BASE_URL (Dateien)
            mapping_table['DATA_URL'] = mapping_table['BASE_URL']

            if online_fallback and self.server.online_fallback:
                mapping_table['BASE_URL'] = self.server.online_fallback

            mapping_templates = {
                'TEMPLATE:HTML_HEADER':         'html.header.html',
                'TEMPLATE:HTML_FOOTER':         'html.footer.html',
                'TEMPLATE:NAVIGATION':          'navigation.html'
            }

            for match in matches:
                if match not in mapping_table.keys(): continue
                content = content.replace('{'+match+'}', mapping_table[match])

            for match in matches:
                if match not in mapping_templates.keys(): continue
                try: replace_with = self.read_from_file(self.server.www + '/dashboard/parts/' + mapping_templates[match])
                except IOError:
                    if self.server.online_fallback:
                        replace_with = self.read_from_fallback(
                            self.server.online_fallback +
                            '/dashboard/parts/' +
                            mapping_templates[match]
                        )
                    else:
                        replace_with = ""
                except Exception: replace_with = ""
                content = content.replace(
                    '{'+match+'}',
                    replace_with or ""
                )

        except Exception as exp:
            logger.exception(exp)
        finally:
            return content