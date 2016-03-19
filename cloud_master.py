#!/usr/bin/env python
#
# A tool to manipulate clouds.
#
# Author: Peter Pakos <peter.pakos@wandisco.com>

from __future__ import print_function
from sys import stderr, argv
from os import path
from argparse import ArgumentParser
from boto3 import Session
from botocore import exceptions
from prettytable import PrettyTable
from datetime import datetime
from abc import ABCMeta, abstractmethod


class Main(object):
    _version = '16.3.19'
    _name = path.basename(argv[0])

    def __init__(self):
        self._profile_name = None
        self._cloud_provider = None
        self._disable_border = False
        self._disable_header = False
        self._region = None
        self._state = []
        action = self.parse_args()
        self._cloud = Cloud.loader(self._cloud_provider, self._profile_name, self._region)
        if action == 'list-instances':
            self._cloud.list_instances(disable_border=self._disable_border,
                                       disable_header=self._disable_header,
                                       state=self._state)
        elif action == 'list-regions':
            self._cloud.list_regions(disable_border=self._disable_border, disable_header=self._disable_header)
        else:
            self.die('Action %s not implemented yet' % action)

    def display_version(self):
        print('%s version %s' % (self._name, self._version))

    @staticmethod
    def die(message=None, code=1):
        if message is not None:
            print(message, file=stderr)
        exit(code)

    def parse_args(self):
        parser = ArgumentParser(description='A tool to manipulate clouds.')
        parser.add_argument('-v', '--version',
                            help='show version', action='store_true', dest='version')
        parser.add_argument('action', nargs='?', choices=['list-instances', 'list-regions'])
        parser.add_argument('-c', '--cloud-provider', help='cloud provider (default: %(default)s)',
                            dest='cloud_provider', choices=['aws', 'gce', 'azure'], default='aws')
        parser.add_argument('-p', '--profile-name', help='cloud profile name (default: %(default)s)',
                            dest='profile_name', default='default')
        parser.add_argument('-b', '--disable-border', help='disable table border', action='store_true',
                            dest='disable_border')
        parser.add_argument('-H', '--disable-header', help='disable table header', action='store_true',
                            dest='disable_header')
        parser.add_argument('-r', '--region', help='choose single region (default: all)', dest='region')
        parser.add_argument('-s', '--state', help='display instances only in certain states', action='append',
                            choices=['running', 'pending', 'shutting-down', 'stopped', 'stopping', 'terminated'],
                            dest='state')
        args = parser.parse_args()
        if args.version:
            self.display_version()
            exit()
        if not args.action:
            parser.print_usage()
            exit()
        self._profile_name = args.profile_name
        self._cloud_provider = args.cloud_provider
        self._disable_border = args.disable_border
        self._disable_header = args.disable_header
        self._region = args.region
        self._state = args.state
        return args.action


class Cloud(object):
    __metaclass__ = ABCMeta

    def __init__(self, cloud_provider, profile_name, region):
        self._cloud_provider = cloud_provider
        self._profile_name = profile_name
        self._region = region

    @staticmethod
    def loader(cloud_provider, profile_name, region):
        classes = {'aws': AWS, 'azure': AZURE, 'gce': GCE}
        return classes[cloud_provider](cloud_provider, profile_name, region)

    @abstractmethod
    def list_instances(self):
        pass

    @abstractmethod
    def list_regions(self):
        pass


class AWS(Cloud):
    def __init__(self, *args, **kwargs):
        super(AWS, self).__init__(*args, **kwargs)
        self._session = None
        try:
            self._session = Session(profile_name=self._profile_name)
        except exceptions.ProfileNotFound as err:
            Main.die(err.message)
        ec2c = self._session.client('ec2')
        self._regions = []
        regions = None
        try:
            regions = ec2c.describe_regions()
        except exceptions.EndpointConnectionError as err:
            Main.die(err.message)
        for region in regions['Regions']:
            self._regions.append(region['RegionName'])
        if self._region:
            if self._region not in self._regions:
                Main.die('Region must be one of the following:\n- %s' %
                         '\n- '.join(self._regions))
            else:
                self._regions = [self._region]

    @staticmethod
    def get_value(list_a, key, value, search_value):
        name = ''
        if type(list_a) == list:
            for item in list_a:
                if item[key] == search_value:
                    name = item[value]
                    break
        return name

    @staticmethod
    def date_diff(date1, date2):
        diff = (date1-date2)
        diff = (diff.microseconds + (diff.seconds + diff.days * 24 * 3600) * 10**6) / 10**6
        d = divmod(diff, 86400)
        h = divmod(d[1], 3600)
        m = divmod(h[1], 60)
        s = m[1]
        diff = []
        if d[0] > 0:
            diff.append('%dd' % d[0])
        if h[0] > 0:
            diff.append('%dh' % h[0])
        if m[0] > 0:
            diff.append('%dm' % m[0])
        diff.append('%ds' % s)
        diff = ' '.join(diff)
        return diff

    def list_instances(self, disable_border=False, disable_header=False, state=None):
        if not state:
            state = ['running', 'pending', 'shutting-down', 'stopped', 'stopping', 'terminated']
        table = PrettyTable(['Zone', 'ID', 'Name', 'Type', 'State', 'Launch time', 'Uptime', 'Key name', 'Private IP',
                             'Public IP'],
                            border=not disable_border, header=not disable_header, reversesort=True,
                            sortby='Launch time')
        table.align = 'l'
        i = 0
        states = dict()
        for region in self._regions:
            ec2r = self._session.resource('ec2', region_name=region)
            instances = ec2r.instances.filter(Filters=[{
                'Name': 'instance-state-name',
                'Values': state
            }])
            for instance in instances:
                i += 1
                name = self.get_value(instance.tags, 'Key', 'Value', 'Name')
                launch_time = str(instance.launch_time).partition('+')[0]
                now = datetime.now()
                then = datetime.strptime(launch_time, '%Y-%m-%d %H:%M:%S')
                uptime = self.date_diff(now, then)
                table.add_row([
                    instance.placement['AvailabilityZone'],
                    instance.id,
                    name,
                    instance.instance_type,
                    instance.state['Name'],
                    launch_time,
                    uptime,
                    instance.key_name,
                    instance.private_ip_address,
                    instance.public_ip_address
                ])
                if instance.state['Name'] in states:
                    states[instance.state['Name']] += 1
                else:
                    states[instance.state['Name']] = 1
        print(table)
        out = ', '.join(['%s: %s' % (key, value) for (key, value) in sorted(states.items())])
        if len(out) > 0:
            out = '(%s)' % out
        else:
            out = ''
        print('Number of instances: %s %s' % (i, out))

    def list_regions(self, disable_border=False, disable_header=False):
        table = PrettyTable(['Region'], border=not disable_border, header=not disable_header, sortby='Region')
        table.align = 'l'
        for region in self._regions:
            table.add_row([region])
        print(table)


class AZURE(Cloud):
    def __init__(self, *args, **kwargs):
        super(AZURE, self).__init__(*args, **kwargs)
        Main.die('%s module not implemented yet, exiting...' % self._cloud_provider.upper())

    def list_instances(self):
        pass

    def list_regions(self):
        pass


class GCE(Cloud):
    def __init__(self, *args, **kwargs):
        super(GCE, self).__init__(*args, **kwargs)
        Main.die('%s module not implemented yet, exiting...' % self._cloud_provider.upper())

    def list_instances(self):
        pass

    def list_regions(self):
        pass


if __name__ == '__main__':
    app = Main()