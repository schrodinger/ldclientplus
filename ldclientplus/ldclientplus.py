
import sys
import re
import json
import importlib.util
from getpass import getpass
import subprocess
from datetime import datetime
import urllib3
from requests.exceptions import HTTPError, ConnectionError
import logging
from time import sleep
from random import randint

from .DynamicLDClient import DynamicLDClient

logger = logging.getLogger(__name__)
# reduce noise level
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class LDClientPlus(DynamicLDClient):
    """
    Child class of BaseLDClient. BaseLDClient provides the api attribute
    corresponding to the LDClient class. This class is built to update
    all protocols to use a Schrodinger Suite variable.
    """
    def __init__(self, host, username, password=None):
        super().__init__(host, username, password, extra_modules=['models', 'client'])
    
    def get_default_schrodinger_suite_version(self):
        # TODO crude, is there a better way?
        cmd = ['ssh', self.host, 'cat', '/mnt/schrodinger/version.txt']
        sp = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        stdout, stderr = sp.communicate()
        if stderr:
            raise RuntimeError(stderr.decode())
        stdout = stdout.decode()
        m = re.match(f'Schrodinger Suite (20[12][0-9]-[1-4])', stdout)
        if not m:
            raise ValueError(f'"{" ".join(cmd)}" returned: "{stdout}".')
        return m.group(1)
    
    def get_ld_version(self):
        return self.api.about()["seurat_job_name"]
    
    def get_ld_properties(self):
        conf_dct = {}
        for key_val in self.api.config():
            # some values contain linebreaks, which makes for ugly csv.
            # I hope it's safe to remove them
            val = key_val['value'].replace('\r', '').replace('\n', '')
            conf_dct[key_val['key']] = val
        return conf_dct
    
    def retry_api_call(self, func, lr_id, retry=10, action="", **kwargs):
        '''
        Repeat a LDClient API call when it fails due to overload.
        Currently only supports calls that use the LR ID as first argument.
        '''
        for n in range(retry):
            logger.debug(f"{action} LR {lr_id} trial {n}")
            try:
                return func(lr_id, **kwargs)
            except (HTTPError, ConnectionError, self.extra_modules.client.ServerProcessingError) as e:
                logger.exception(e)
                logger.exception(f"Error {action} LR {lr_id}. Retrying...")
                sleep(randint(1, 10)) # Wait a random 1-10s delay
        logger.error(f"Giving up on {action} LR {lr_id} after {retry} tries.")
        return None

    def dump_live_report(self, lr_id, report_level=None, experiment_column_id=None, use_cached=False, retry=10):
        lr_id = str(lr_id)
        try:

            if report_level is None:
                lr = self.retry_api_call(self.api.live_report_metadata, lr_id, action="getting LR metadata for")
                report_level = getattr(lr, 'report_level', 'PARENT')
                experiment_column_id = getattr(lr, 'experiment_column_id', None)

            logger.debug(f"Dumping LR: ID {lr_id}, report level {report_level}%s." % f", experiment column ID {experiment_column_id}" if experiment_column_id else "")
            
            results = self.retry_api_call(self.api.execute_live_report, lr_id,
                                    retry=retry,
                                    action="executing",
                                    params={'use_cached': use_cached,
                                            'report_level': report_level,
                                            'experiment_addable_column_id': experiment_column_id},
                                    max_tries=retry)
            if results is None:
                logger.error(f"Could not execute LiveReport {lr_id}.")
                return
            metadata = self.retry_api_call(self.api.live_report_results_metadata, lr_id,
                                    retry=retry,
                                    action="getting results metadata for",
                                    params={"report_level": report_level,
                                            "experiment_addable_column_id": experiment_column_id})
            if metadata is None:
                logger.error(f"Could not get results metadata for LiveReport {lr_id}.")
                return
            current_csv = self.retry_api_call(self.api.export_live_report, lr_id,
                                        retry=retry, action="exporting", export_type='csv')
            if current_csv is None:
                logger.error(f"Could not export LiveReport {lr_id}.")
                return
        except Exception:
            logger.exception(f"Encountered unusual exception when exporting LR {lr_id}.")
            return
        return current_csv
    
    def dump_live_report_to_file(self, lr_id, filename, report_level=None, experiment_column_id=None, use_cached=False, retry=10):
        csv = self.dump_live_report(lr_id, report_level=None, experiment_column_id=None, use_cached=False, retry=10)
        if len(csv) > 0:
            with open(filename, 'wb') as fh:
                fh.write(csv)
        else:
            logger.error(f"LR {lr_id} dump was empty.")
