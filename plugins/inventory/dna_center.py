# Copyright (c) 2019 World Wide Technology
# GNU General Public License v3.0+
# (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
from ansible.errors import AnsibleError, AnsibleParserError
from ansible.plugins.inventory import BaseInventoryPlugin

DOCUMENTATION = r'''
    name: wwt.ansible_dnac.dna_center
    plugin_type: inventory
    short_description: Returns Inventory from DNA Center
    description:
        - Retrieves inventory from DNA Center
        - Adds inventory to ansible working inventory

    options:
        plugin:
            description: Name of the plugin
            required: true
            choices: ['dna_center']
        host:
            description: FQDN of the target host
            required: true
        username:
            description: user credential for target system
            required: true
        password:
            description: user pass for the target system
            required: true
        validate_certs:
            description: certificate validation
            required: false
            choices: ['yes', 'no']
        use_dnac_mgmt_int:
            description: map the dnac mgmt interface to `ansible_host`
            required: false
            default: true
            choices: [true, false]
'''

EXAMPLES = r'''
    ansible-inventory --graph

    ansible-inventory --list
'''

try:
    import requests
except ImportError:
    raise AnsibleError("Python requests module is required for this plugin.")


class InventoryModule(BaseInventoryPlugin):

    NAME = 'dna_center'

    def __init__(self):
        super(InventoryModule, self).__init__()

        # from config
        self.username = None
        self.password = None
        self.host = None
        self.session = None
        self.use_dnac_mgmt_int = None

        # global attributes
        self._site_list = None
        self._inventory = None
        self._host_list = None

    def _login(self):
        '''
            :return Login results from the request.
        '''
        login_url = 'https://' + self.host + '/dna/system/api/v1/auth/token'
        self.session = requests.session()
        self.session.auth = self.username, self.password
        self.session.verify = False
        self.session.headers.update({'Content-Type': 'application/json'})

        try:
            login_results = self.session.post(login_url)
        except Exception as e:
            raise AnsibleError('failed to login to DNA Center: {}'.format(e))

        if login_results.status_code not in [200, 201, 202, 203, 204]:
            raise AnsibleError('failed to login. \
                    Status code was not in the 200s')
        else:
            self.session.headers.update({'x-auth-token':
                                        login_results.json()['Token']})

            return login_results

    def _get_inventory(self):
        '''
            :return The json output from the request object response.
        '''

        inventory_url = 'https://' + self.host + \
            '/dna/intent/api/v1/network-device'
        inventory_results = self.session.get(inventory_url)

        self._inventory = inventory_results.json()

        return inventory_results.json()

    def _get_hosts(self):
        '''
             :param inventory A list of dictionaries representing
                    the entire DNA Center inventory.
             :return A List of tuples that include the management IP,
                    device hostnanme, and the unique indentifier of the device.
        '''

        host_list = []

        for host in self._inventory['response']:
            if host['type'].find('Access Point') == -1:
                host_dict = {}
                host_dict.update({
                    'managementIpAddress': host['managementIpAddress'],
                    'hostname': host['hostname'],
                    'id': host['id'],
                    'os': host['softwareType'],
                    'version': host['softwareVersion']
                })
                host_list.append(host_dict)

        self._host_list = host_list

        return host_list

    def _get_sites(self):
        '''
            :return A list of tuples for sites containing the site name
                and the unique ID of the site.
        '''
        site_url = 'https://' + self.host + \
            '/dna/intent/api/v1/topology/site-topology'
        site_results = self.session.get(site_url)

        sites = site_results.json()['response']['sites']

        site_list = []
        site_dict = {}

        for site in sites:

            site_dict = {}
            site_dict.update({'name': site['name'].replace(' ', '_').lower(),
                              'id': site['id'], 'parentId': site['parentId']})
            site_list.append(site_dict)

        self._site_list = site_list

        return site_list

    def _get_member_site(self, device_id):
        '''
            :param device_id: The unique identifier of the target device.
            :return A single string representing the name of the SITE group
              of which the device is a member.
        '''

        url = 'https://' + self.host + \
            '/dna/intent/api/v1/topology/physical-topology?nodeType=device'
        results = self.session.get(url)
        devices = results.json()['response']['nodes']

        # Get the one device we are looking for
        device = [dev for dev in devices if dev['id'] == device_id][0]

        # Extract the siteid from the device data
        site_id = device.get('additionalInfo').get('siteid')

        # set the site name from the self._site_list
        site_name = [site['name'] for site in self._site_list
                     if site['id'] == site_id]

        # return the name if it exists
        if len(site_name) == 1:
            return site_name[0]
        elif len(site_name) == 0:
            return 'ungrouped'

    def _add_sites(self):
        ''' Add groups and associate them with parent groups
            :param site_list: list of group dictionaries containing name, id,
             parentId
        '''

        # Global is a system group and the parent of all top level groups
        site_ids = [ste['id'] for ste in self._site_list]
        parent_name = ''

        # Add all sites
        for site in self._site_list:
            self.inventory.add_group(site['name'])

        # Add parent/child relationship
        for site in self._site_list:

            if site['parentId'] in site_ids:
                parent_name = [ste['name'] for ste in self._site_list
                               if ste['id'] == site['parentId']][0]

                try:
                    self.inventory.add_child(parent_name, site['name'])
                except Exception as e:
                    raise AnsibleParserError('adding child sites failed:  {} \n {}:{}'.format(e, site['name'], parent_name))

    def _add_hosts(self):
        """
            Add the devicies from DNAC Inventory to the Ansible Inventory
            :param host_list: list of dictionaries for hosts retrieved from
                DNAC

        """
        for h in self._host_list:
            site_name = self._get_member_site(h['id'])
            if site_name:
                self.inventory.add_host(h['hostname'], group=site_name)

                #  add variables to the hosts
                if self.use_dnac_mgmt_int:
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_host',
                                                h['managementIpAddress'])

                self.inventory.set_variable(h['hostname'], 'os', h['os'])
                self.inventory.set_variable(h['hostname'],
                                            'version',
                                            h['version'])
                if h['os'].lower() in ['ios', 'ios-xe']:
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_network_os',
                                                'ios')
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_connection',
                                                'network_cli')
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_become',
                                                'yes')
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_become_method',
                                                'enable')
                elif h['os'].lower() in ['nxos', 'nx-os']:
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_network_os',
                                                'nxos')
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_connection',
                                                'network_cli')
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_become',
                                                'yes')
                    self.inventory.set_variable(h['hostname'],
                                                'ansible_become_method',
                                                'enable')
            else:
                raise AnsibleError('no site name found for host: {} with site_id {}'.format(h['id'], self._site_list))

    def verify_file(self, path):

        valid = False
        if super(InventoryModule, self).verify_file(path):
            if path.endswith(('dnac.yaml',
                              'dnac.yml',
                              'dna_center.yaml',
                              'dna_center.yml')):
                valid = True
        return valid

    def parse(self, inventory, loader, path, cache=True):

        super(InventoryModule, self).parse(inventory, loader, path, cache)

        # initializes variables read from the config file based on the
        # documentation string definition.
        # If the options are not defined in the docstring, the are not
        # imported from config file
        self._read_config_data(path)

        # Set options values from configuration file
        try:
            self.host = self.get_option('host')
            self.username = self.get_option('username')
            self.password = self.get_option('password')
            self.map_mgmt_ip = self.get_option('use_dnac_mgmt_int')
        except Exception as e:
            raise AnsibleParserError('getting options failed:  {}'.format(e))

        # Attempt login to DNAC
        login_results = self._login()
        if login_results.status_code not in [200, 201, 202, 203]:
            raise AnsibleError('failed to login: {}'.format(login_results.status_code))

        # Obtain Inventory Data
        self._get_inventory()

        # Add groups to the inventory
        self._get_sites()
        self._add_sites()

        # Add the hosts to the inventory
        self._get_hosts()
        self._add_hosts()
