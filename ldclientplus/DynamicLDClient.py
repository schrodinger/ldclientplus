import sys
import os
import re
import tempfile
import tarfile
import importlib
from getpass import getpass
import logging
import requests
import glob
import shutil

logger = logging.getLogger(__name__)

class ExtraModules(dict):
    """
    dict subclass that allows accessing keys as attributes. Used to store
    extra ldclient modules in BaseLDClient
    """
    def __getattr__(self, name):
        if name in self:
            return self[name]
    
    def __setattr__(self, name, value):
        self[name] = self.from_nested_dict(value)
    
    def __delattr__(self, name):
        if name in self:
            del self[name]
    
    @staticmethod
    def from_nested_dict(data):
        """ Construct nested ExtraModules from nested dictionaries. """
        if not isinstance(data, dict):
            return data
        else:
            return ExtraModules({key: ExtraModules.from_nested_dict(data[key])
                                for key in data})

class DynamicLDClient():
    """
    Download and import ldclient dynamically. BaseLDClient.api then contains
    the ldclient.LDClient interface. Extra ldclient modules can be imported as
    well using the extra_modules argument. For example
    extra_modules = ['models', 'api.paths'] would provide those modules as
    BaseLDClient.extra_modules.models and BaseLDClient.extra_modules.api.paths

    @param host: FQDN/URL of the LD instance.
    @type  host: str

    @param username: username for LD instance.
    @type  username: str

    @param password: password for username.
    @type  password: str
    
    @param extra_modules: List of extra modules to import from ldclient.
                          For example ['model']
    """
    
    def __init__(self, host, username=None, password=None, extra_modules=None):
        self.host = host
        self.username = username
        self.password = password or self._prompt_password()
        
        # set up ldclient API
        self._tmp_dir = tempfile.mkdtemp()
        self._ldclient_dir = None
        self.extra_modules = ExtraModules()
        self.api = self._get_api(extra_modules=extra_modules)
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
    
    def _prompt_password(self):
        prompt = f"Enter password for {self.username} on {self.host}: "
        return getpass(prompt)
    
    def _load_ldclient_module(self, module_name, module_file):
        module_path = os.path.join(self._ldclient_dir, module_file)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    
    def _hack_replace_ldclient_module_name(self, ldclient_module_name):
        for fn in ('models.py', 'requests.py'):
            try:
                with open(os.path.join(self._ldclient_dir, ldclient_module_name, fn), 'r') as fin:
                    models = fin.read()
                models = models.replace('from ldclient', f'from {ldclient_module_name}')
                with open(os.path.join(self._ldclient_dir, ldclient_module_name, fn), 'w') as fout:
                    fout.write(models)
            except IOError:
                pass
    
    def _get_ldclient_version(self):
        # gets ldclient version (x.x.x) before import
        fn = os.path.join(self._ldclient_dir, 'ldclient_version.py')
        try:
            with open(fn, 'r') as fin:
                for line in fin:
                    m = re.match("VERSION = [\"']([0-9]+.[0-9]+.[0-9]+)[\"']", line)
                    if m:
                        return m.group(1)
                else:
                    raise RuntimeError(f"Could not determine ldclient version from {fn}")
        except IOError:
            logger.exception("ldclient_version.py not present. Can't determine ldclient version.")

    def _get_api(self, extra_modules=None):
        try:
            if not self.host.startswith("http"):
                ld_server = f"https://{self.host}"
            else:
                ld_server = self.host
            
            ### Download
            tar_filename = 'ldclient.tar.gz'
            ldclient_path = '/livedesign/' + tar_filename
            url = ld_server + ldclient_path
            # Fetch the tar file using requests
            filename = os.path.join(self._tmp_dir, tar_filename)
            try:
                # don't wait forever...
                logger.debug(f"Downloading LDClient from {url}")
                r = requests.get(url, verify=False, timeout=10)
            except Exception as e:
                logger.error(f"Could not download {url}: {e}")
                raise
            
            with open(filename, "wb") as f:
                f.write(r.content)
            # Un-tar the tar file into a temporary folder
            tar = tarfile.open(filename)
            tar.extractall(path=self._tmp_dir)
            tar.close()
            
            ### Import
            # Construct the path to un-compressed file
            path = os.path.join(self._tmp_dir, 'ldclient-*')
            try:
                self._ldclient_dir = glob.glob(path)[0]
            except IndexError:
                logger.error("Could not find location of extracted ldclient package.")
                raise
            
            ldclient_version = self._get_ldclient_version()
            ldclient_module_name = f"ldclient_{ldclient_version.replace('.', '')}"

            os.rename(os.path.join(self._ldclient_dir, 'ldclient'), os.path.join(self._ldclient_dir, ldclient_module_name))
            
            self._hack_replace_ldclient_module_name(ldclient_module_name)
            
            importlib.invalidate_caches()
            sys.path.insert(0, self._ldclient_dir)
            
            try:
                ldclient = self._load_ldclient_module(ldclient_module_name, f'{ldclient_module_name}/__init__.py')
            except ImportError as e:
                logger.exception(f"Could not import ldclient: {e}")
                raise
            # Import extra modules and store them in self.extra_modules
            # Chained modules will be accessible as
            # self.extra_modules.parent.child, using the ExtraModules class
            for mod in extra_modules:
                mod_parts = mod.split('.')
                logger.debug(mod_parts)
                try:
                    module  = self._load_ldclient_module(f'{ldclient_module_name}.{mod}', f'{ldclient_module_name}/{"/".join(mod_parts)}.py')
                    mod_dict = module
                    for part in mod_parts[1:]:
                        mod_dict = {part: mod_dict}
                    setattr(self.extra_modules, mod_parts[0], mod_dict)
                    
                except ImportError as e:
                    logger.exception(f"Could not import ldclient.{mod}: {e}")
                    raise

            # keep path clean
            sys.path.remove(self._ldclient_dir)
            
            ### Login
            # API setup and check if working.
            if ld_server.endswith('localhost'):
                ld_server += ":9081"
            endpoint = f"{ld_server}/livedesign/api"
            
            logger.debug(f"Logging in to {self.host} as {self.username} ...")
            
            try:
                api = ldclient.LDClient(endpoint, self.username, self.password)
                api.ping()
            except Exception as e:
                logger.error(f"Could not connect to LD: {e}")
                raise
            
            return api
        except:
            self.close()
            raise
    
    def close(self):
        try:
            sys.path.remove(self._ldclient_dir)
        except (ValueError, TypeError):
            pass
        if self._tmp_dir is not None and os.path.exists(self._tmp_dir):
            shutil.rmtree(self._tmp_dir)
