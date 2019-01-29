#!/usr/bin/env python
# Generate TheHive alerts
#
# Author: Remi Seguy <remg427@gmail.com>
#
# Copyright: LGPLv3 (https://www.gnu.org/licenses/lgpl-3.0.txt)
# Feel free to use the code, but please share the changes you've made

# most of the code here was based on the following example on splunk custom alert actions
# http://docs.splunk.com/Documentation/Splunk/6.5.3/AdvancedDev/ModAlertsAdvancedExample

import ConfigParser
import csv
import datetime
import gzip
import json
import os
import requests
import sys
import time
from splunk.clilib import cli_common as cli
from requests.auth import HTTPBasicAuth
import logging

__author__     = "Remi Seguy"
__license__    = "LGPLv3"
__version__    = "1.01"
__maintainer__ = "Remi Seguy"
__email__      = "remg427@gmail.com"


def prepare_config(config, filename):
    config_args = {}
    # open thehive.conf
    thehiveconf = cli.getConfStanza('thehive','thehivesetup')
    # get the thehive_url we need to connect to thehive
    # this can be passed as params of the alert. Defaults to values set in thehive.conf  
    # get specific thehive url and key if any (from alert configuration)
    thehive_url = config.get('thehive_url')
    thehive_key = config.get('thehive_key')   
    if thehive_url and thehive_key:
        thehive_url = str(thehive_url)
        thehive_verifycert = int(config.get('thehive_verifycert', "0"))
        if thehive_verifycert == 1:
            thehive_verifycert = True
        else:
            thehive_verifycert = False
    else: 
        # get thehive settings stored in thehive.conf
        thehive_url = str(thehiveconf.get('thehive_url'))
        thehive_key = thehiveconf.get('thehive_key')
        if thehiveconf.get('thehive_verifycert') == 1:
            thehive_verifycert = True
        else:
            thehive_verifycert = False

    # check and complement config
    config_args['thehive_url'] = thehive_url
    config_args['thehive_key'] = thehive_key
    # get proxy parameters if any
    http_proxy = thehiveconf.get('http_proxy', '')
    https_proxy = thehiveconf.get('https_proxy', '')
    if http_proxy != '' and https_proxy != '':
        config_args['proxies'] = {
            "http": http_proxy,
            "https": https_proxy
        }
    else:
        config_args['proxies'] = {}

    # Get numeric values from alert form
    config_args['tlp'] = int(config.get('tlp'))
    config_args['severity'] = int(config.get('severity'))

    # Get string values from alert form
    myTemplate = config.get('caseTemplate', "default")
    if myTemplate in [None, '']:
        config_args['caseTemplate'] = "default"
    else:
        config_args['caseTemplate'] = myTemplate
    myType = config.get('type', "alert")
    if myType in [None, '']:
        config_args['type'] = "alert"
    else:
        config_args['type'] = myType
    mySource =  config.get('source')
    if mySource in [None, '']:
        config_args['source'] = "splunk"
    else:
        config_args['source'] = mySource
    if not config.get('unique'): 
        config_args['unique'] = "oneEvent"
    else:
        config_args['unique'] = config.get('unique')
    if not config.get('title'):
        config_args['title'] = "notable event"
    else:
        config_args['title'] = config.get('title')
    myDescription = config.get('description')
    if myDescription in [None, '']:
        config_args['description'] = "No description provided."
    else:
        config_args['description'] =  myDescription
    myTags = config.get('tags')
    logging.info("myTags %s", myTags)
    if myTags in [None, '']:
        config_args['tags'] = []
    else:
        tags = []
        tag_list = myTags.split(',')
        for tag in tag_list:
            if tag not in tags:
                tags.append(tag)
        logging.info("split tags %s", tags)
        config_args['tags'] = tags
    

    # add filename of the file containing the result of the search
    config_args['filename'] = filename
    return config_args


def create_alert(config, results):

    # iterate through each row, cleaning multivalue fields and then adding the attributes under same alert key
    # this builds the dict alerts
    # https://github.com/TheHive-Project/TheHiveDocs/tree/master/api

    alerts = {}
    alertRef = 'SPK' + str(int(time.time()))

    for row in results:
    
        # Splunk makes a bunch of dumb empty multivalue fields - we filter those out here 
        row = {key: value for key, value in row.iteritems() if not key.startswith("__mv_")}

        # find the field name used for a unique identifier and strip it from the row
        if config['unique'] in row:
            id = config['unique']
            sourceRef = str(row.pop(id)) # grabs that field's value and assigns it to our sourceRef 
        else:
            sourceRef = alertRef

        # check if attributes have been stored for this sourceRef. If yes, retrieve them to add new ones from this row
        if sourceRef in alerts:
            alert = alerts[sourceRef]
            attributes = alert["attributes"]
        else:
            alert = {}
            attributes = {} 

        # attributes can be provided in two ways
        # - either a table with columns type and value
        # - or a table with one column per type and value in the cell (it can be empty for some rows)
        # 
        # they are collected and added to the dict in the format data:dataType
        # using data as key avoids duplicate entries if some field values are common to several rows with the same sourceRef!
        
        # check if row has columns type and value
        if 'type' in row and 'value' in row:
            mykey   = str(row.pop('type'))
            myvalue = str(row.pop('value'))
            if myvalue != "":
                print >> sys.stderr, "DEBUG key %s value %s" % (mykey, myvalue) 
                attributes[myvalue] = mykey
                # now we take the others KV pairs if any to add to dict 
                for key, value in row.iteritems():
                    if value != "":
                        print >> sys.stderr, "DEBUG key %s value %s" % (key, value) 
                        attributes[str(value)] = key
        
        # if there is one column per type in results
        else:
        # now we take those KV pairs to add to dict 
            for key, value in row.iteritems():
                if value != "":
                    print >> sys.stderr, "DEBUG key %s value %s" % (key, value) 
                    attributes[str(value)] = key
    
        if attributes:
            alert['attributes'] = attributes
            alerts[sourceRef] = alert

    # actually send the request to create the alert; fail gracefully
    try:

        # iterate in dict alerts to create alerts
        for srcRef, attributes in alerts.items():
            print >> sys.stderr, "DEBUG sourceRef is %s and attributes are %s" % (srcRef, attributes)

            artifacts = []

            # now we take those KV pairs and make a list-type of dicts 
            for value, dType in attributes['attributes'].items():
                artifacts.append(dict(
                    dataType = dType,
                    data = value,
                    message = "%s observed in this alert" % dType
                ))

            payload = json.dumps(dict(
                title = config['title'],
                description = config['description'],
#                tags = config['tags'],
                severity = config['severity'],
                tlp = config['tlp'],
                type = config['type'],
                artifacts = artifacts,
                source = config['source'],
                caseTemplate = config['caseTemplate'],
                sourceRef = srcRef # I like to use eval id=md5(_raw) 
            ))

            # set proper headers
            url  = config['thehive_url']
            auth = config['thehive_key']

            headers = {'Content-type': 'application/json'}
            headers['Authorization'] = 'Bearer ' + auth
            headers['Accept'] = 'application/json'

            print >> sys.stderr, 'DEBUG Calling url="%s" with headers %s and payload=%s' % (url, headers, payload) 
            # post alert
            response = requests.post(url, headers=headers, data=payload, verify=False, proxies=config['proxies'])
            print >> sys.stderr, "INFO theHive server responded with HTTP status %s" % response.status_code
            # check if status is anything other than 200; throw an exception if it is
            response.raise_for_status()
            # response is 200 by this point or we would have thrown an exception
            print >> sys.stderr, "DEBUG theHive server response: %s" % response.json()
    
    # somehow we got a bad response code from thehive
    except requests.exceptions.HTTPError as e:
        print >> sys.stderr, "ERROR theHive server returned following error: %s" % e
    # some other request error occurred
    except requests.exceptions.RequestException as e:
        print >> sys.stderr, "ERROR Error creating alert: %s" % e
        
    
if __name__ == "__main__":
    # set up logging suitable for splunkd consumption
    logging.root
    logging.root.setLevel(logging.DEBUG)    
    # make sure we have the right number of arguments - more than 1; and first argument is "--execute"
    if len(sys.argv) > 1 and sys.argv[1] == "--execute":
        # read the payload from stdin as a json string
        payload = json.loads(sys.stdin.read())
        # extract the file path and alert config from the payload
        configuration = payload.get('configuration')
        filename = payload.get('results_file')
        # test if the results file exists - this should basically never fail unless we are parsing configuration incorrectly
        # example path this variable should hold: '/opt/splunk/var/run/splunk/12938718293123.121/results.csv.gz'
        if os.path.exists(filename):
            # file exists - try to open it; fail gracefully
            try:
                # open the file with gzip lib, start making alerts
                # can with statements fail gracefully??
                with gzip.open(filename) as file:
                    # DictReader lets us grab the first row as a header row and other lines will read as a dict mapping the header to the value
                    # instead of reading the first line with a regular csv reader and zipping the dict manually later
                    # at least, in theory
                    Reader = csv.DictReader(file)
                    logging.debug('Reader is %s', str(Reader))
                    Config = prepare_config(configuration,filename)
                    logging.debug('Config is %s', json.dumps(Config))
                    # make the alert with predefined function; fail gracefully
                    create_alert(Config, Reader)
                # by this point - all alerts should have been created with all necessary observables attached to each one
                # we can gracefully exit now
                sys.exit(0)
            # something went wrong with opening the results file
            except IOError as e:
                print >> sys.stderr, "FATAL Results file exists but could not be opened/read"
                sys.exit(3)
        # somehow the results file does not exist
        else:
            print >> sys.stderr, "FATAL Results file does not exist"
            sys.exit(2)
    # somehow we received the wrong number of arguments
    else:
        print >> sys.stderr, "FATAL Unsupported execution mode (expected --execute flag)"
        sys.exit(1)
