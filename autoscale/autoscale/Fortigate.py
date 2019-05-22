import boto3
from botocore.exceptions import ClientError
import json
from .const import *
import base64
import tempfile
import urllib3
from .fos_api import FortiOSAPI
from urllib.parse import urlparse
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Fortigate(object):
    def __init__(self, data, asg):
        self.data = data
        if data is not None:
            if 'Message' not in data:
                return
            msg = json.loads(data['Message'])
            self.msg = msg
        else:
            self.msg = {}
        self.fgt = None
        self.ec2 = None
        self.lch_token = None
        self.lch_transition = None
        self.lch_name = None
        self.timestamp = None
        self.status = None
        self.az = None
        self.public_subnet_id = None
        self.second_nic_id = None
        self.private_subnet_id = None
        self.auto_scale_group = asg
        self.api = None
        self.sg = None
        self.instance_id = None
        self.ec2_client = asg.ec2_client
        self.s3_client = asg.s3_client
        self.s3_resource = asg.s3_resource
        self.ec2_resource = asg.ec2_resource
        self.auto_scale_group = asg
        self.version = None
        self.config_object = None
        if 'Details' in self.msg:
            if 'Availability Zone' in self.msg['Details']:
                self.az = self.msg['Details']['Availability Zone']
            if 'Subnet ID' in self.msg['Details']:
                self.public_subnet_id = self.msg['Details']['Subnet ID']
        if 'Progress' in self.msg:
            self.progress = self.msg['Progress']

        if 'LifecycleActionToken' in self.msg:
            self.lch_token = self.msg['LifecycleActionToken']
            logger.info("Fortigate init(): token = %s" % self.lch_token)
        if 'LifecycleTransition' in self.msg:
            self.lch_transition = self.msg['LifecycleTransition']
        if 'LifecycleHookName' in self.msg:
            self.lch_name = self.msg['LifecycleHookName']
        if 'Time' in self.msg:
            self.timestamp = self.msg['Time']
        if 'EC2InstanceId' in self.msg:
            self.instance_id = self.msg['EC2InstanceId']
            self.update_ec2_info()
            if self.ec2 is None:
                return
            if 'SecurityGroups' in self.ec2:
                if len(self.ec2['SecurityGroups']) > 0:
                    self.sg = self.ec2['SecurityGroups'][0]['GroupId']
            if 'SubnetId' in self.ec2:
                self.public_subnet_id = self.ec2['SubnetId']
            for i in self.ec2['NetworkInterfaces']:
                index = i['Attachment']['DeviceIndex']
                if index == 1:
                    self.private_subnet_id = i['SubnetId']
                    self.second_nic_id = i['NetworkInterfaceId']

        # TODO: fill in Fortigate init when instance is already in DB
        return

    def __repr__(self):
        return ' () ' % ()

    #
    # Use FortiOS API restore function to restore a config stored on an S3 bucket
    #
    def config_firewall(self):
        if self.s3_client is None:
            self.s3_client = boto3.client('s3')
        fp = tempfile.NamedTemporaryFile()
        config_object = self.config_object
        o = urlparse(config_object)
        bucket = o[1].lstrip('/')
        path = o[2].lstrip('/')
        with open(fp.name, 'wb') as content:
            try:
                self.s3_client.download_fileobj(bucket, path, content)
            except Exception as ex:
                logger.exception("caught: %s with config_firewall(): ex = %s" % ex)
                return
        fd = open(fp.name, 'r')
        cfg = fd.read()
        c = base64.b64encode(cfg)
        fd.close()
        data = {'source': 'upload', 'file_content': c, 'scope': 'global'}
        params = {'vdom': 'root'}
        self.api.post(api='monitor', path='system', name='config',
                      action='restore', data=data, parameters=params, mkey=None)
        return

    #
    # Do everything necessary to get a ScaleOut instance ready to run
    #
    def make_instance_ready(self):
        #
        # get the firmware version from the 'get system status' command.
        # This will be used to format API calls that changed post 5.4.0
        #
        if self.ec2 is None:
            return
        ip = None
        if 'PublicIpAddress' in self.ec2:
            ip = self.ec2['PublicIpAddress']
        elif 'PrivateIpAddress' in self.ec2:
            ip = self.ec2['PrivateIpAddress']
        self.api = FortiOSAPI()
        self.api.login(ip, 'admin', self.instance_id)
        content = self.api.get(api='monitor', path='system', name='firmware', action='select', mkey=None)
        data = json.loads(content)
        if 'version' in data:
            value = data['version']
            ver = value.replace("v", "")
            v = ver.replace(".", "")
            self.version = int(v)
        #
        # Note: This might go away if config-init bootstrap works.
        #
        # Find the tag that points to a config file used to bootstrap this device
        #
        self.config_object = self.get_tag('s3-config-path')
        if self.config_object is None:
            return
        #
        # Restore the config file to the Fortigate
        #
        self.config_firewall()
        return

    def update_ec2_info(self):
        self.ec2 = None
        logger.info('update_ec2_info(): id = %s' % self.instance_id)
        try:
            instances = self.ec2_client.describe_instances(InstanceIds=[self.instance_id])
        except Exception as ex:
            logger.exception("Fortigate.exception(): message = %s, instance = %s" % (ex, self.instance_id))
            return
        if 'ResponseMetadata' not in instances or instances['ResponseMetadata']['HTTPStatusCode'] != STATUS_OK:
            logger.info('update_ec2_info1():')
            return
        if 'Reservations' not in instances or len(instances['Reservations']) == 0:
            logger.info('update_ec2_info2():')
            return
        for instance in instances['Reservations']:
            for i in instance['Instances']:
                logger.info('update_ec2_info3(): ec2_info.instance_id = %s' % i['InstanceId'])
                if i['InstanceId'] == self.instance_id:
                    self.ec2 = i

    #
    # Find a tag that matches key on this Fortigate
    #
    def get_tag(self, key):
        if self.ec2 is None:
            self.update_ec2_info()
        if 'Tags' not in self.ec2:
            return None
        tags = self.ec2['Tags']
        for t in tags:
            if t['Key'] == key:
                return t['Value']
        return None

    def get_instance_state(self):
        return self.ec2['State']['Name']

    #
    # Delete the second interface for the ENI contained in the JSON message
    #
    def delete_second_interface(self, item):
        #
        # Pull the Lifecycle Information
        #
        t = self.auto_scale_group.table
        self.lch_name = item['LifecycleHookName']
        self.lch_token = item['LifecycleToken']
        #
        # Describe the network interface and see if it is "available"
        #
        logger.info("delete_second_interface(): eni = %s" % item['ENIId'])
        try:
            r = self.ec2_client.describe_network_interfaces(NetworkInterfaceIds=[item['ENIId']])
        except Exception as ex:
            logger.exception("describe_network_interface(): message = %s" % ex)
            #
            # If the ENI is not there, delete the info from the database so we don't try this again.
            #
            t.delete_item(TableName=self.auto_scale_group.name,
                          Key={"Type": TYPE_ENI_ID, "TypeId": item['ENIId']})

            return
        for i in r['NetworkInterfaces']:
            #
            # If the ENI is no longer attached (i.e. 'available'), delete it. Also, respond to the lifecycle event
            # and allow the instance to change state to "Proceed:terminate"
            if i['Status'] == 'available':
                logger.info("delete_second_interface(): eni = %s is available" % item['ENIId'])
                self.ec2_client.delete_network_interface(NetworkInterfaceId=item['ENIId'])
                self.lch_action('CONTINUE')
                try:
                    t.delete_item(TableName=self.auto_scale_group.name,
                                  Key={"Type": TYPE_ENI_ID, "TypeId": item['ENIId']})
                except Exception as ex:
                    logger.exception("delete_second_interface(): message = %s, instance = %s" %
                                     (ex, self.instance_id))
        return

    #
    # lch_terminate: Detach the interface and write an entry to the DB. It takes about 60 seconds before the
    # ENI is 'available' and can be deleted. The ENI will be deleted using the DB entry on the scheduled thread.
    #
    def detach_second_interface(self):
        attachment_id = None
        nic_id = None
        for i in self.ec2['NetworkInterfaces']:
            index = i['Attachment']['DeviceIndex']
            if index == 1:
                attachment_id = i['Attachment']['AttachmentId']
                nic_id = i['NetworkInterfaceId']
        if nic_id is None:
            self.lch_action('CONTINUE')
            return
        if attachment_id is not None:
            try:
                r = self.ec2_client.detach_network_interface(AttachmentId=attachment_id, Force=True)
            except Exception as ex:
                logger.exception("detach_network_interface(): message = %s" % ex)
                return
            if r is None:
                return
        t = self.auto_scale_group.table
        self.auto_scale_group.asg = {"Type": TYPE_ENI_ID, "TypeId": nic_id,
                                     "AutoScaleGroupName": self.auto_scale_group.name,
                                     "LifecycleHookName": self.lch_name, "LifecycleToken": self.lch_token,
                                     "ENIId": nic_id}
        t.put_item(Item=self.auto_scale_group.asg)
        return

    def add_member_to_autoscale_group(self, master_ip):
        callback_url = self.auto_scale_group.endpoint_url + "/" + "callback/" + self.auto_scale_group.name
        if self.ec2['PrivateIpAddress'] == master_ip:
            data = {
                  "status": "enable",
                  "role": "master",
                  "sync-interface": "port1",
                  "psksecret": self.auto_scale_group.name,
                  "callback-url": callback_url
            }
            self.ec2_client.create_tags(Resources=[self.instance_id],
                                        Tags=[{'Key': 'Fortinet-Autoscale', 'Value': 'Master'}])
            logger.info('posting auto-scale config: {}' .format(data))
            self.api = FortiOSAPI()
            try:
                self.api.login(self.ec2['PublicIpAddress'], 'admin', self.instance_id)
            except Exception as ex:
                logger.exception("login.exception(): message = %s, instance = %s" % (ex, self.instance_id))
                return
            content = self.api.put(api='cmdb', path='system', name='auto-scale', data=data)
            logger.info('restapi response: {}' .format(content))
            self.api.logout()
        else:
            data = {
                  "status": "enable",
                  "role": "slave",
                  "master-ip": master_ip,
                  "sync-interface": "port1",
                  "psksecret": self.auto_scale_group.name,
                  "callback-url": callback_url
            }
            self.ec2_client.create_tags(Resources=[self.instance_id],
                                        Tags=[{'Key': 'Fortinet-Autoscale', 'Value': 'Slave'}])
            logger.info('posting auto-scale config: {}' .format(data))
            self.api = FortiOSAPI()
            try:
                self.api.login(self.ec2['PublicIpAddress'], 'admin', self.instance_id)
            except Exception as ex:
                logger.exception("login.exception(): message = %s, instance = %s" % (ex, self.instance_id))
                return
            content = self.api.put(api='cmdb', path='system', name='auto-scale', data=data)
            logger.info('restapi response: {}' .format(content))
            self.api.logout()
        return

    def attach_second_interface(self, subnets):
        #
        # If no interface information, this instance is messed up and needs to be ABANDONED
        #
        logger.info("attach_second_interface(): instance_id = %s, subnets = %s" % (self.instance_id, subnets))
        self.update_ec2_info()
        if 'NetworkInterfaces' not in self.ec2:
            logger.info("attach_second_interface0(): ABANDON")
            return STATUS_NOT_OK
        #
        # This is just a second request for this instance and second interface has already been added
        # Just respond OK and keep going. No need to ABANDON or CONTINUE
        #
        if len(self.ec2['NetworkInterfaces']) == 2:
            logger.info("attach_second_interface1(): two interfaces found")
            return STATUS_NOT_OK
        if len(self.ec2['NetworkInterfaces']) == 0:
            logger.info("attach_second_interface2(): no interfaces found")
            return STATUS_NOT_OK
        #
        # Something is wrong with the metadata. Check the cloudformation template or terraform.
        #
        if len(subnets) != 4:
            self.lch_action('ABANDON')
            return STATUS_NOT_OK
        if self.public_subnet_id == subnets[0]:
            self.private_subnet_id = subnets[1]
        if self.public_subnet_id == subnets[2]:
            self.private_subnet_id = subnets[3]
        nic = self.ec2_client.create_network_interface(Groups=[self.sg],
                                                       SubnetId=self.private_subnet_id,
                                                       Description='Second Network Interface')
        logger.info("create_network_interface3(): nic = %s" % nic)

        #
        # Something bad happened in the create. ABANDON
        #
        if 'NetworkInterface' not in nic:
            self.lch_action('ABANDON')
        self.second_nic_id = nic['NetworkInterface']['NetworkInterfaceId']
        if self.second_nic_id is None:
            self.lch_action('ABANDON')
        snic = self.ec2_resource.NetworkInterface(self.second_nic_id)
        #
        # Something bad happened in the attach. ABANDON
        #
        if snic is None:
            self.lch_action('ABANDON')
        logger.info("ec2_resource(): snic = %s" % snic)
        #
        # TODO: May want to add delete on termination attribute to second interface
        # for now, it's easier to LCH_TERMINATION without it
        #
        snic.modify_attribute(SourceDestCheck={'Value': False})
        #
        # Attach the new interface to the instance
        #
        try:
            a = self.ec2_client.attach_network_interface(NetworkInterfaceId=self.second_nic_id,
                                                         InstanceId=self.instance_id, DeviceIndex=1)
        except Exception as ex:
            logger.exception("attach_network_interface(): message = %s, instance = %s"
                             % (ex, self.instance_id))
            self.ec2_client.delete_network_interface(NetworkInterfaceId=self.second_nic_id)
            return None

        if a is None or 'AttachmentId' not in a:
            self.lch_action('ABANDON')

        self.update_ec2_info()
        name1 = self.auto_scale_group.name + "_AutoScale_" + self.instance_id + "_ENI0"
        name2 = self.auto_scale_group.name + "_AutoScale_" + self.instance_id + "_ENI1"
        #
        # Everything looks good. TAG the interfaces and complete the LifeCycleAction
        #
        nic_id = self.ec2['NetworkInterfaces'][0]['NetworkInterfaceId']
        self.ec2_client.create_tags(Resources=[nic_id], Tags=[{'Key': 'Name', 'Value': name1}])
        nic_id = self.ec2['NetworkInterfaces'][1]['NetworkInterfaceId']
        self.ec2_client.create_tags(Resources=[nic_id], Tags=[{'Key': 'Name', 'Value': name2}])
        #
        # This was there because FortiOS < 6.0.4 required a reboot before the NIC was recognized
        # That is not the case any longer
        # t = self.auto_scale_group.table
        # self.auto_scale_group.asg = {"Type": TYPE_ENI_ID, "TypeId": self.second_nic_id,
        #                              "AutoScaleGroupName": self.auto_scale_group.name,
        #                              "LifecycleHookName": self.lch_name, "LifecycleToken": self.lch_token,
        #                              "ENIId": self.second_nic_id}
        # t.put_item(Item=self.auto_scale_group.asg)
        self.lch_action('CONTINUE')
        return STATUS_OK

    #
    # Respond to a lifecycle action with action = 'ABANDON' or 'CONTINUE'
    #
    def lch_action(self, action):
        if action == 'ABANDON' and self.second_nic_id is not None:
            self.ec2_client.delete_network_interface(NetworkInterfaceId=self.second_nic_id)
        try:
            self.auto_scale_group.asg_client.complete_lifecycle_action(LifecycleHookName=self.lch_name,
                                                                       AutoScalingGroupName=self.auto_scale_group.name,
                                                                       LifecycleActionToken=self.lch_token,
                                                                       LifecycleActionResult=action)
        except ClientError:
            logger.exception("Invalid complete_life_cycle_action(): ClientError, instance = %s" % self.instance_id)
            pass
        return

def find_s3_license_file(self, bucket):
    s3c = self.s3_client
    s3r = self.s3_resource
    s3buckets = s3c.list_buckets()
    s3configbucket = fa.cft.resource_ids['S3Bucket']
    s3licensebucket = fa.cft.resource_ids['S3LicenseBucket']
    s3cbucket_exists = False
    s3lbucket_exists = False
    if 'Buckets' in s3buckets:
        for b in s3buckets['Buckets']:
            if 'Name' in b:
                if b['Name'] == s3configbucket:
                    s3cbucket_exists = True
                if b['Name'] == s3licensebucket:
                    s3lbucket_exists = True
    if s3cbucket_exists is False or s3lbucket_exists is False:
        return None
    working_bucket = fa.cft.resource_ids['S3Bucket']
    s3_full_path = "licenses/" + instance.instance_id + ".lic"
    objects = s3c.list_objects(Bucket=working_bucket, Prefix="licenses/")
    if 'Contents' in objects:
        for o in objects['Contents']:
            if 'Key' in o:
                p = o['Key']
                s = p.split('/')
                if len(s) == 2:
                    suf = s[1].split('.')
                    if len(suf) == 2:
                        if suf[1] == "lic":
                            if s[1] not in fa.unused_licenses:
                                new = fa.ec2client.describe_instances(InstanceIds=[instance.instance_id])
                                state = new['Reservations'][0]['Instances'][0]['State']['Name']
                                if state == 'running':
                                    fa.unused_licenses.append(s[1])
    path = instance.instance_id + ".lic"
    if path in fa.unused_licenses:
        return instance.instance_id
    bucket = fa.cft.resource_ids['S3LicenseBucket']
    if s3c is None:
        return None
    if bucket is None:
        return None
    objects = s3c.list_objects(Bucket=bucket)
    if 'Contents' not in objects:
        return None
    unused_license_key = None
    for o in objects['Contents']:
        suffix = o['Key'].split('.')
        if len(suffix) == 2 and suffix[1] == 'lic':
            unused_license_key = o['Key']
            break
    if unused_license_key is None:
        return None
    b = s3r.Bucket(bucket)
    local_path = "licenses/" + instance.instance_id + ".lic"
    license_directory = os.path.dirname(local_path)
    try:
        os.stat(license_directory)
    except:
        os.mkdir(license_directory)
    o = b.Object(unused_license_key)
    with open(local_path, 'wb') as content:
        try:
            status = o.download_fileobj(content)
        except Exception, ex:
            print "caught: %s with FindS3License" % ex.message

    s3_full_path = "licenses/" + instance.instance_id + ".lic"
    working_bucket = fa.cft.resource_ids['S3Bucket']
    with open(local_path, 'rb') as content:
        status = s3c.upload_fileobj(content, working_bucket, s3_full_path)
    s3path = instance.instance_id + ".lic"
    os.remove(local_path)
    fa.unused_licenses.append(s3path)
    s3c.delete_object(Bucket=bucket, Key=unused_license_key)
    return instance.instance_id
