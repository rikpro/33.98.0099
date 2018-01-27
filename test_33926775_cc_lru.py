'''
Created on 4-nov.-2016

@author: AST

Property of Televic Rail
'''

import sys
import tftpy

import time
import StringIO
import traceback
import os
import subprocess
from wx.lib.pubsub import setupkwargs   # to force Version 3 of pubsub
from wx.lib.pubsub import pub
import wx
import telnetlib

sys.path.append('..\\33.97.1063')       # yav_board_lib
sys.path.append('..\\33.97.1069')       # testsystem_setup
sys.path.append('..\\33.96.7535')       # TestSystemSubGui 
sys.path.append('..\\33.98.0109')
sys.path.append('..\\33.97.1038')
sys.path.append('..\\33.98.0103')
sys.path.append('..\\33.97.1027')

telnet_port = 23
telnet_timeout = 5
reading_timeout = 5
PROMPT1 = '\n$>'
PROMPT2 = '\nD$>'
PROMPT3 = 'D$>'

import yav_board_lib as yav
import testsystem_setup
import Logger
import cc_telnet
import database
import RS485
import serial
try:
    import tlv_cc_test_scen_config
    import cc_config
    
    import cisco_sf300_connector
    import net.ping_lib

    import database
    from testsystem_main import TestSystemSubGui as TSG
except Exception, e:
    print 'ERROR %s: Import local libraries/configs failed:' % e
    sys.exit(1)
    
netsw_ip = tlv_cc_test_scen_config.netsw_ip
netsw_user = tlv_cc_test_scen_config.netsw_user
netsw_password = tlv_cc_test_scen_config.netsw_password
netsw_config = tlv_cc_test_scen_config.netsw_config
netsw_reload_wait = tlv_cc_test_scen_config.netsw_reload_wait
netsw_telnet_port = 23
netsw_telnet_timeout= 5
test_host_ip = tlv_cc_test_scen_config.test_host_ip
tftp_svc_port = int(tlv_cc_test_scen_config.tftp_svc_port)

ping_count = tlv_cc_test_scen_config.ping_count
ping_packet_size = tlv_cc_test_scen_config.ping_packet_size
ping_timeout = tlv_cc_test_scen_config.ping_timeout
ping_udp = tlv_cc_test_scen_config.ping_udp
nping_exe = tlv_cc_test_scen_config.nping_exe

upload_timeout = tlv_cc_test_scen_config.upload_timeout
on_time = tlv_cc_test_scen_config.on_time
wait_timer = tlv_cc_test_scen_config.wait_timer
tlv_cc_ip = tlv_cc_test_scen_config.tlv_cc_ip
tlv_rtr_uc_telnet_port = tlv_cc_test_scen_config.tlv_rtr_uc_telnet_port

data_dir_cisco_sf300 = os.path.abspath('../33.98.0105')

tlv_cc_pmdb = cc_config.tlv_cc_pmdb


def ping_to_ip(test_ip, on_time, ping_count, ping_packet_size, ping_timeout, ping_udp, wait_timer):
    """
    This is a function to run a ping test to a specified ip-address
        Args:
            param1 (str):
            param2 (int): 
            param3 (str): 
        Returns:
            
        Raises:
    """
    
    # Initializing variables
    running = True
    host_up = False
    ping_result = dict()
    
    while running:

        while not host_up:
            ping_result.clear()
            
            try:
                subprocess.Popen(["arp.exe","-d",test_ip],stdout = subprocess.PIPE, stderr=subprocess.STDOUT).communicate()[0]           
            except subprocess.CalledProcessError as e:
                out_bytes = e.output       # Output generated before error
                code      = e.returncode   # Return code
                return 1
            time.sleep(wait_timer)
            
            
            try:
                ping_result = net.ping_lib.py_ping(test_ip, ping_timeout, ping_packet_size, ping_count, ping_udp, quiet_output=True)
                
                if ping_result['ret_code'] == 0:
                    host_up = True
                else:
                    return ping_result
                
            except Exception, err:
                print err
                return 1
       
        running = False
        
    return ping_result
    
    
class PWR_STATE:
    OFF = 0
    ON = 1
    
class TELNET_STATE:
    OFF = 0
    ON = 1

class test33926775LRU(object):
    """ Module B4 (yav90132) """
    __version__ = "DEV_33980095_1-01-01"
    
    POWER_RELAY_CHANNELS_MASK_BAT1 = [yav.RELAY_NONE, yav.RELAY_NONE, yav.RELAY_NONE, (yav.RELAY1 | yav.RELAY7)]
    POWER_RELAY_CHANNELS_MASK_BAT2 = [yav.RELAY_NONE, yav.RELAY_NONE, yav.RELAY_NONE, (yav.RELAY4 | yav.RELAY6)]
    
    ALLOFF = [yav.RELAY_NONE, yav.RELAY_NONE, yav.RELAY_NONE, yav.RELAY_NONE]
    
    MASK_ALL = [yav.RELAY_ALL, yav.RELAY_ALL, yav.RELAY_ALL, yav.RELAY_ALL]
    
    pwrState = PWR_STATE.OFF
    telnetState = TELNET_STATE.OFF
    
    def __init__(self, voltage, current, 
                 ipAddress = "80.0.0.2",
                 login = "",
                 password = ""):
         
        self.testSetup = testsystem_setup.TestSetup33988041()
        self.logger = Logger.testLogger()
        
        self.BAT_VOLTAGE = voltage/2
        self.LIMIT_CURRENT = current
        self.ip = ipAddress
        self.ccTn = cc_telnet.cc_telnet(ipAddress, port = 23,
                                        login = login,
                                        password = password)
        
        self.time_out = 8
        
        self.routerTelnetSetup()
        self.servicePort = False
        
        self.debug_mode = False
        self.debug_mode_next = False
        pub.subscribe(self.enterDebugMode, 'ENTER_DEBUG_MODE')
        pub.subscribe(self.goToNextBreakpoint, 'NEXT_BREAKPOINT')
        
    
    def enterDebugMode(self, mode):
        if self.debug_mode and mode == False:
            self.debug_mode = False
            self.logger.freeText('***** STOPPING DEBUGMODE *****\n')
        elif not self.debug_mode and mode == True:
            self.debug_mode = True
            self.logger.freeText('***** STARTING DEBUGMODE *****\n')

    def goToNextBreakpoint(self, mode):
        self.debug_mode_next = True

    def checkDebugMode(self, text = ''):
        if self.debug_mode:
            pub.sendMessage("ON_BREAKPOINT")
            self.logger.freeText('***** BREAKPOINT REACHED *****\n')
            if text != '':
                self.logger.freeText(text)
            while self.debug_mode_next == False and self.debug_mode == True:
                time.sleep(1)
        self.debug_mode_next = False 
        
    def routerTelnetSetup(self):
        try:
            self.netsw_telnet = cisco_sf300_connector.cisco_sf300_telnet_login(netsw_ip, 
                                                                               netsw_telnet_port, 
                                                                               netsw_telnet_timeout, 
                                                                               netsw_user, 
                                                                               netsw_password, 
                                                                               wait_timer)
            self.logger.freeText('Opened telnet connection to: ' + netsw_ip)
            self.routerConnection = True
        except Exception, err:
            self.logger.freeText('Failed to opened a telnet connection to: %s', netsw_ip)
            return False
        
        return True
        
    def closeRouterTelnet(self):
        try:
            cisco_sf300_connector.cisco_sf300_telnet_close(self.netsw_telnet)
            self.logger.freeText('Closed the telnet connection to: ' + netsw_ip)
            self.routerConnection = False
            #time.sleep(wait_timer)
        except Exception, err:
            self.logger.freeText('Failed to close the telnet connection to: %s', netsw_ip)
            
    def openRouterPort(self, Port):
        cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf=Port, intf_state='no shutdown')
            
    def openservicePort(self):
        cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf='GE3', intf_state='no shutdown')
        self.servicePort = True
        
    def closeRouterPort(self, Port):
        cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf=Port, intf_state='shutdown')
            
    def closeservicePort(self):
        cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf='GE3', intf_state='shutdown')
        self.servicePort = False
        
    def closeAllRouterPorts(self):
        for i in range(24):
            cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf="FE" + str(i), intf_state='shutdown')
        cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf="GE3", intf_state='shutdown')
        self.servicePort = False
        
    def FullPowerOn(self, Voltage = None):
        """ Turn On the power and set the switches for Both circuits """      
        self.logger.freeText('Power on DUT, wait a few seconds')  
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT1
        retval = self.testSetup.yav90132.setMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT2
        retval = self.testSetup.yav90132.setMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        if Voltage == None:
            self.testSetup.powerOnPs4Left(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
            self.testSetup.powerOnPs4Right(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
        else:
            self.testSetup.powerOnPs4Left(Voltage, self.LIMIT_CURRENT)
            self.testSetup.powerOnPs4Right(Voltage, self.LIMIT_CURRENT)
        
        time.sleep(5)
        time.sleep(self.time_out)
        self.pwrState = PWR_STATE.ON
        
    def PowerOnBat1(self):
        """ Turn On the power and set the switches for BAT1 """        
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT1         
        retval = self.testSetup.yav90132.setMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        
        self.testSetup.powerOnPs4Left(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
        self.testSetup.powerOnPs4Right(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
        
        time.sleep(2)
        
    def PowerOnBat2(self):
        """ Turn On the power and set the switches for BAT2 """
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT2         
        retval = self.testSetup.yav90132.setMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        
        self.testSetup.powerOnPs4Left(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
        self.testSetup.powerOnPs4Right(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
        
        time.sleep(2)
        
    def FullPowerOff(self):
        """ Turn Off the power and reset the relays """
        time.sleep(2)
        self.testSetup.powerOffPs4Left()
        self.testSetup.powerOffPs4Right()
        
        time.sleep(2)
        
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT1
        retval = self.testSetup.yav90132.clearMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT2
        retval = self.testSetup.yav90132.clearMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        
        self.pwrState = PWR_STATE.OFF
        
    def Reset(self):
        self.logger.freeText('Repower DUT, wait a few seconds')
#         time.sleep(2)
        
        self.testSetup.powerOffPs4Left()
        self.testSetup.powerOffPs4Right()
        
        time.sleep(3)
        
        self.testSetup.powerOnPs4Left(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
        self.testSetup.powerOnPs4Right(self.BAT_VOLTAGE, self.LIMIT_CURRENT)
        
        time.sleep(self.time_out)
        time.sleep(5)
        
    def PowerOffBat1(self):
        """ Turn Off the power and reset the relays """
        self.testSetup.powerOffPs4Left()
        self.testSetup.powerOffPs4Right()
        
        time.sleep(2)
        
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT1
        retval = self.testSetup.yav90132.clearMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        
    def PowerOffBat2(self):
        """ Turn Off the power and reset the relays """
        self.testSetup.powerOffPs4Left()
        self.testSetup.powerOffPs4Right()
        
        time.sleep(2)
        
        relayChannels = self.POWER_RELAY_CHANNELS_MASK_BAT2
        retval = self.testSetup.yav90132.clearMultiple(relayChannels[0], relayChannels[1], relayChannels[2], relayChannels[3])
        
    def PowerOnIOcard(self):
        self.testSetup.powerOnPs1(24,0.3)
        
    def PowerOffIOcard(self):
        self.testSetup.powerOffPs1()
        
    def PowerOnChassis(self):
        self.testSetup.powerOnPs3Left(5,0.5)
        
    def PowerOffChassis(self):
        self.testSetup.powerOffPs3Left()
        
    def resetAllRelays(self):
        self.testSetup.yav90132.clearAll()
        self.testSetup.yav904X8.clearAll()
        self.testSetup.yav90132_B3.clearAll()
        self.testSetup.yav904X8_A3.clearAll()
        
    def Redundant_Power_Check(self, minVal = 100, maxVal = 200):
        """
        Test both power paths
        """
        
        self.logger.testTitle("Check Powersupplies")
        
        l_retval = []
        l_TestStepNumber = 1

###    Test BAT1    ###
        self.PowerOnBat1()
        currentConsBat1 = self.testSetup.getPs4RightCurrent()
        if currentConsBat1 >= minVal and currentConsBat1 <= maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            
        l_TestStepDescription = "Test path for BAT1"
        l_TestStepCriterium = ("Current consumption for BAT1 should be between %dmA and %dmA" 
                               % (minVal, maxVal))
        
        l_TestStepResult = str(currentConsBat1) + "mA"
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.PowerOffBat1()
        
###    Test BAT2    ###       
        self.PowerOnBat2()
        currentConsBat2 = self.testSetup.getPs4RightCurrent()
        
        if currentConsBat2 >= minVal and currentConsBat2 <= maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            
        l_TestStepDescription = "Test path for BAT2"
        l_TestStepCriterium = ("Current consumption for BAT2 should be between %dmA and %dmA" % (minVal, maxVal))
        l_TestStepResult = str(currentConsBat2) + "mA"
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.PowerOffBat2()
        
        return l_retval
    
    def Miniumum_Power_Check(self):
        """
        Test if device works at 60% of it's nominal voltage
        """
        self.logger.testTitle("Check if CC works at minimum voltage")
        
        l_retval = []
        l_TestStepNumber = 1        
        retval = True
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn(Voltage=self.BAT_VOLTAGE*0.6)
        elif self.pwrState == PWR_STATE.ON:
            self.ccTn.EnterDegradeMode()
            self.ccTn.Close()
            self.FullPowerOff()
            self.FullPowerOn(Voltage=self.BAT_VOLTAGE*0.6)
            
        self.ccTn.Connect()
        time.sleep(0.1)
        response = self.ccTn.Read(0x41, 1)
        
        if response == "FFFF" or response == "ffff":
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            
        l_TestStepDescription = "Check if the DUT responds at minimum power"
        l_TestStepCriterium = "The response of the DUT should be FFFF"
        l_TestStepResult = "Response is " + response
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.ccTn.Close()
        self.FullPowerOff()
        
        return l_retval
    
    def VersionCheck(self, App, IfFpga, IpFpga, Jingles, Config):
        """
        Check the versions of the CC
        """
        S01Versions = []
        S01Versions.append(App)
        S01Versions.append(IfFpga)
        S01Versions.append(IpFpga)
        S01Versions.append(Jingles)
        S01Versions.append(Config)
        
        sw = ["app", "fpga if board", "fpga ip board", "Jingles", "Config"]
        
        self.logger.testTitle("Check the SW and SW versions")
        
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        
        versions = self.ccTn.ReadVersions()
        Versions = StringIO.StringIO(versions)
        Versions.readline()
        
        for i in range(5):
            vers = Versions.readline()
            print "Version should be " + S01Versions[i] + "    Version should be " + vers[0:len(vers)-5]
            if vers[0:len(vers)-5] == S01Versions[i]:
                print "PASS"
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
            else:
                print "FAIL"
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                
            l_TestStepDescription = ("Check SW and SW version for %s" % sw[i])
            l_TestStepCriterium = ("Should be %s" % S01Versions[i])
            l_TestStepResult = vers[0:len(vers)-5]
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        
        return l_retval
        
    def X11_Input_Test(self):
        """
        Test input pins of the X11 connector
        """
        self.logger.testTitle("Check Inputs of X11")
        
        l_retval = []
        l_TestStepNumber = 1
        retval = True
        
        responses = ["FFF0","FF0F","F0FF"]
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()        

            
        self.checkDebugMode("Power ")
        self.ccTn.Connect()
        time.sleep(3)
        self.ccTn.ExitDegradeMode()
        time.sleep(3)
        
        self.PowerOnIOcard()
        self.testSetup.pio.clearAllSourcedOutputs()
        try:
            for i in range(3):
                self.testSetup.yav90132.setSingle(1+i)
                
                self.testSetup.pio.setSourcedOutput(1+i)
                self.testSetup.pio.setSourcedOutput(4+i)
                self.testSetup.pio.setSourcedOutput(7+i)
                self.testSetup.pio.setSourcedOutput(10+i)
                
                time.sleep(3)
                
                resp = self.ccTn.Read(0x33, 1)
                
                if resp[3-i] == "0":
                    l_TestStepConclusion = "PASS"
                    l_retval.append(True)
                else:
                    l_TestStepConclusion = "FAIL"
                    l_retval.append(False)
                    
                self.testSetup.yav90132.clearSingle(1+i)
                self.testSetup.pio.clearAllSourcedOutputs()
                l_TestStepDescription = ("Check INP%d_1, INP%d_2, INP%d_3 and INP%d_4" % (i+1,i+1,i+1,i+1))
                l_TestStepCriterium = ("The response of the DUT should be 0")#%s" % responses[i])
                l_TestStepResult = "Response is " + resp[3-i]
                self.logger.structured(l_TestStepNumber, 
                                       l_TestStepDescription, 
                                       l_TestStepCriterium, 
                                       l_TestStepResult, 
                                       l_retval[l_TestStepNumber-1])
                l_TestStepNumber = l_TestStepNumber + 1
        except:
            self.logger.freeText("ERROR: Could not read registers")
            l_retval.append(False)
        
        self.PowerOffIOcard()
        
        return l_retval
    
    def X11_Output_Test(self):
        """
        Test Output relays of connector X11
        """
        self.logger.testTitle("Check Outputs of X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.PowerOnIOcard()
        
        self.testSetup.pio.setUpperThreshhold(15)
        self.testSetup.pio.setLowerThreshhold(10)
        self.ccTn.Write(0x42, 0x30, True)
        
        self.testSetup.yav90132.setSingle(17)
        self.testSetup.yav90132.setSingle(18)
        
        time.sleep(0.2)
        
        for i in range(2):
            out = self.testSetup.pio.checkInput(9+i)
            if out == 1:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = "The output value is HIGH"
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = "The output value is LOW"
                
            l_TestStepDescription = ("Check OUT%d" % (i+1))
            l_TestStepCriterium = ("The output should be HIGH")
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
        self.testSetup.yav90132.clearSingle(17)
        self.testSetup.yav90132.clearSingle(18)
        self.ccTn.Write(0x42, 0x0, True)
        
        self.PowerOffIOcard()
        
        return l_retval
    
    def X10_IO_Test(self):
        """
        Test IO pins of connector X10
        """
        self.logger.testTitle("Check IO of X10")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.PowerOnIOcard()
        
        self.testSetup.pio.clearAllSinkedOutputs()
        self.testSetup.pio.clearAllSourcedOutputs()        
        self.testSetup.pio.setUpperThreshhold(20)
        self.testSetup.pio.setLowerThreshhold(10)
        
###    Test Outputs        
        self.ccTn.Write(0x43, 0xa000, True)
        time.sleep(0.2)
        
        out = []
        for i in range(2):
            out.append(self.testSetup.pio.checkInput(2-i))
            if out[i] == 1:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = "The output value is HIGH"
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = "The output value is LOW"
                
            l_TestStepDescription = ("Check output function of IO%d" % (2+i))
            l_TestStepCriterium = ("The output should be HIGH")
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        
        self.ccTn.Write(0x43, 0x0, True)
        
###    Test Inputs        
        self.testSetup.pio.setSourcedOutput(13)
        self.testSetup.pio.setSourcedOutput(17)
        self.testSetup.pio.setSourcedOutput(18)
        
        time.sleep(3)
        
        resp = self.ccTn.Read(0x32, True)
        if resp == "0888":
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The response is %s" % resp)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The response is %s" % resp)
            
        l_TestStepDescription = ("Check input funtionality of IO pins")
        l_TestStepCriterium = ("The response should be 0888")
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
            
        self.testSetup.pio.clearAllSourcedOutputs()            
        
            
        self.PowerOffIOcard()
        
        return l_retval
        
    def X9_IO_Test(self):
        """
        Test IO pins of connector X9
        """
        self.logger.testTitle("Check IO of X9")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.PowerOnIOcard()
        
        self.testSetup.pio.clearAllSinkedOutputs()
        self.testSetup.pio.clearAllSourcedOutputs()        
        self.testSetup.pio.setUpperThreshhold(20)
        self.testSetup.pio.setLowerThreshhold(10)
        
###    Test Outputs        
        self.ccTn.Write(0x43, 0x5000, True)     
        time.sleep(0.2)
        
        out = []
        for i in range(2):
            out.append(self.testSetup.pio.checkInput(4-i))
            if out[i] == 1:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = "The output value is HIGH"
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = "The output value is LOW"
                
            l_TestStepDescription = ("Check output function of IO%d" % (2+i))
            l_TestStepCriterium = ("The output should be HIGH")
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        self.ccTn.Write(0x43, 0x0, True)
        
###    Test Inputs        
        self.testSetup.pio.setSourcedOutput(14)
        self.testSetup.pio.setSourcedOutput(19)
        self.testSetup.pio.setSourcedOutput(20)
        
        time.sleep(3)
        resp = self.ccTn.Read(0x32, True)
        if resp == "0444":
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The response is %s" % resp)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The response is %s" % resp)
            
        l_TestStepDescription = ("Check input funtionality of IO pins")
        l_TestStepCriterium = ("The response should be 0444")
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
            
        self.testSetup.pio.clearAllSourcedOutputs()            
        
            
        self.PowerOffIOcard()
        
        return l_retval
        
    def X8_IO_Test(self):        
        """
        Test IO pins of connector X8
        """
        self.logger.testTitle("Check IO of X8")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        time.sleep(3)
        self.ccTn.ExitDegradeMode()
        time.sleep(3)
        
        self.PowerOnIOcard()
        
        self.testSetup.pio.clearAllSinkedOutputs()
        self.testSetup.pio.clearAllSourcedOutputs()        
        self.testSetup.pio.setUpperThreshhold(20)
        self.testSetup.pio.setLowerThreshhold(10)
        
###    Test Outputs        
        self.ccTn.Write(0x43, 0xa00, True) 
        time.sleep(3)
        
        out = []
        for i in range(2):
            out.append(self.testSetup.pio.checkInput(6-i))
            if out[i] == 1:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = "The output value is HIGH"
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = "The output value is LOW"
                
            l_TestStepDescription = ("Check output function of IO%d" % (2+i))
            l_TestStepCriterium = ("The output should be HIGH")
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        
        self.ccTn.Write(0x43, 0x0, True)
        
###    Test Inputs
        self.testSetup.pio.setSourcedOutput(15)
        self.testSetup.pio.setSourcedOutput(21)
        self.testSetup.pio.setSourcedOutput(22)
        
        time.sleep(3)
        
        resp = self.ccTn.Read(0x32, True)
        if resp == "0222":
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The response is %s" % resp)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The response is %s" % resp)
            
        l_TestStepDescription = ("Check input funtionality of IO pins")
        l_TestStepCriterium = ("The response should be 0222")
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
            
        self.testSetup.pio.clearAllSourcedOutputs()            
        
            
        self.PowerOffIOcard()
        
        return l_retval
        
    def X7_IO_Test(self):
        """
        Test IO pins of connector X7
        """
        self.logger.testTitle("Check IO of X7")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        time.sleep(3)
        self.ccTn.ExitDegradeMode()
        time.sleep(3)
        
        self.PowerOnIOcard()
        
        self.testSetup.pio.clearAllSinkedOutputs()
        self.testSetup.pio.clearAllSourcedOutputs()
        self.testSetup.pio.setUpperThreshhold(20)
        self.testSetup.pio.setLowerThreshhold(10)
        time.sleep(3)
        
###    Test Outputs        
        self.ccTn.Write(0x43, 0x500, True)  
        time.sleep(3)

        out = []
        for i in range(1):
            out.append(self.testSetup.pio.checkInput(8-i))
            if out[i] == 1:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = "The output value is HIGH"
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = "The output value is LOW"
                
            l_TestStepDescription = ("Check output function of IO%d" % (2+i))
            l_TestStepCriterium = ("The output should be HIGH")
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
        self.ccTn.Write(0x43, 0x0, True)
        
###    Test Inputs        
        self.testSetup.pio.setSourcedOutput(16)
        self.testSetup.pio.setSourcedOutput(23)
        self.testSetup.pio.setSourcedOutput(24)
        
        time.sleep(3)
        
        resp = self.ccTn.Read(0x32, True)
        if resp == "0111":
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The response is %s" % resp)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The response is %s" % resp)
            
        l_TestStepDescription = ("Check input funtionality of IO pins")
        l_TestStepCriterium = ("The response should be 0111")
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
            
        self.testSetup.pio.clearAllSourcedOutputs()            
        
            
        self.PowerOffIOcard()
        
        return l_retval
        
        
    def X10_PER1_Audio_Test(self, minVal = 0.9, maxVal = 1.0):
        """
        Test audio path of peripheral 1 (X10)
        """
        self.logger.testTitle("Check Audio of X10")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav904X8.setSingle(1)
        self.testSetup.yav904X8.setSingle(18)
        
        self.ccTn.Write(0x70000206, 0x20a)
        
        self.testSetup.ap.SetLvlnGainGen(0, 1.0, "Vrms")
        
        result = self.testSetup.ap.GetLvlnGain("Vrms", 5)
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value of X10 (PER1)")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.yav904X8.clearSingle(1)
        self.testSetup.yav904X8.clearSingle(18)
        
        self.ccTn.Write(0x70000206, 0x0)
        
        self.testSetup.ap.turnOfGenerator()
        return l_retval
        
    def X9_PER2_Audio_Test(self, minVal = 0.9, maxVal = 1.0):
        """
        Test audio path of peripheral 2 (X9)
        """
        self.logger.testTitle("Check Audio of X9")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav904X8.setSingle(3)
        self.testSetup.yav904X8.setSingle(20)
        
        self.ccTn.Write(0x70000206, 0x30b)
        
        self.testSetup.ap.SetLvlnGainGen(0, 1.0, "Vrms")
        
        result = self.testSetup.ap.GetLvlnGain("Vrms", 5)
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value of X9 (PER2)")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.yav904X8.clearSingle(3)
        self.testSetup.yav904X8.clearSingle(20)
        
        self.ccTn.Write(0x70000206, 0x0)
        
        self.testSetup.ap.turnOfGenerator()
        return l_retval
        
        
    def X8_PER3_Audio_Test(self, minVal = 0.9, maxVal = 1.0):
        """
        Test audio path of peripheral 3 (X8)
        """
        self.logger.testTitle("Check Audio of X8")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav904X8.setSingle(5)
        self.testSetup.yav904X8.setSingle(22)
        
        self.ccTn.Write(0x70000206, 0x40c)
        
        self.testSetup.ap.SetLvlnGainGen(0, 1.0, "Vrms")
        
        result = self.testSetup.ap.GetLvlnGain("Vrms", 5)
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value of X8 (PER3)")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.yav904X8.clearSingle(5)
        self.testSetup.yav904X8.clearSingle(22)
        
        self.ccTn.Write(0x70000206, 0x0)
        
        self.testSetup.ap.turnOfGenerator()
        return l_retval
        
    def X7_PER4_Audio_Test(self, minVal = 0.9, maxVal = 1.0):
        """
        Test audio path of peripheral 4 (X7)
        """
        self.logger.testTitle("Check Audio of X7")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav904X8.setSingle(7)
        self.testSetup.yav904X8.setSingle(24)
        
        self.ccTn.Write(0x70000206, 0x50d)
        
        self.testSetup.ap.SetLvlnGainGen(0, 1.0, "Vrms")
        
        result = self.testSetup.ap.GetLvlnGain("Vrms", 5)
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value of X7 (PER4)")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.yav904X8.clearSingle(7)
        self.testSetup.yav904X8.clearSingle(24)
        
        self.ccTn.Write(0x70000206, 0x0)
        
        self.testSetup.ap.turnOfGenerator()
        return l_retval
        
    def X11_LS1_Audio_Test(self, inp = 5.0, minVal = 0.8, maxVal = 1.0):
        """
        Test audio path of LS1
        """
        self.logger.testTitle("Check Audio of LS1 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()

        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(27)
        
        self.ccTn.Write(0x70000206, 0x712)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS1")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x700)     
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(27)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval
        
    def X11_LS2_Audio_Test(self, inp = 5.0, minVal = 0.8, maxVal = 1.0):
        """
        Test audio path of LS2
        """
        self.logger.testTitle("Check Audio of LS2 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        self.ccTn.Write(0x42, 0x400, 1)
        
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(28)
        
        self.ccTn.Write(0x70000206, 0x912)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS2")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x900)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(28)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        self.ccTn.Write(0x42, 0x0, 1)
        
        return l_retval        

        
    def X11_LS3_Audio_Test(self, inp = 5.0, minVal = 0.8, maxVal = 1.0):
        """
        Test audio path of LS3
        """
        self.logger.testTitle("Check Audio of LS3 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        self.ccTn.Write(0x42, 0xe00, 1)
        
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(29)
        
        self.ccTn.Write(0x70000206, 0x912)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS3")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x900)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(29)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        self.ccTn.Write(0x42, 0x0, 1)
        return l_retval    
        
    def X11_LS4_Audio_Test(self, inp = 5.0, minVal = 0.8, maxVal = 1.0):
        """
        Test audio path of LS4
        """
        self.logger.testTitle("Check Audio of LS4 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(30)
        
        self.ccTn.Write(0x70000206, 0x812)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        self.checkDebugMode("Wait until level is up")
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
            
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS4")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x800)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(30)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval
        
    def X11_LS5_Audio_Test(self, inp = 5.0, minVal = 0.8, maxVal = 1.0):
        """
        Test audio path of LS5
        """
        self.logger.testTitle("Check Audio of LS5 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        self.ccTn.Write(0x42, 0x800, 1)
        
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(31)
        
        self.ccTn.Write(0x70000206, 0x912)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS5")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x900)
        time.sleep(0.1)        
        
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(31)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        self.ccTn.Write(0x42, 0x0, 1)
        
        return l_retval         
        
    def X11_LS6_Audio_Test(self, inp = 5.0, minVal = 1.9, maxVal = 2.1):
        """
        Test audio path of LS6
        """
        self.logger.testTitle("Check Audio of LS6 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(21)
        self.testSetup.yav90132_B3.setSingle(22)
        self.testSetup.yav90132_B3.setSingle(23)
        self.testSetup.yav90132_B3.setSingle(24)
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(32)
        
        self.ccTn.Write(0x70000206, 0x612)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS6")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x600)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(21)
        self.testSetup.yav90132_B3.clearSingle(22)
        self.testSetup.yav90132_B3.clearSingle(23)
        self.testSetup.yav90132_B3.clearSingle(24)
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(32)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval        
        
    def X10_PowerTest(self, minVal = 23.0, maxVal = 25.0):
        """
        Check power availability X10
        """
        self.logger.testTitle("Check Power on X10")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        names = ["Power", "INP_Power"]
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        result = []
        for i in range(2):
            self.testSetup.yav904X8_A3.setSingle(5+i)
        
            result.append(self.testSetup.ap.GetDCRes())
            if result[i] > minVal and result[i] < maxVal:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = ("The power value is %f" % result[i])
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = ("The power value is %f" % result[i])
                
            l_TestStepDescription = ("Check if %s is available" % names[i])
            l_TestStepCriterium = ("The value should be between %fV and %fV" % (minVal, maxVal))
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
            self.testSetup.yav904X8_A3.clearSingle(5+i)
            time.sleep(0.1)
        
        return l_retval
        
    def X9_PowerTest(self, minVal = 23.0, maxVal = 25.0):
        """
        Check power availability X9
        """
        self.logger.testTitle("Check Power on X9")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        names = ["Power", "INP_Power"]
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        result = []
        for i in range(2):
            self.testSetup.yav904X8_A3.setSingle(7+i)
        
            result.append(self.testSetup.ap.GetDCRes())
            if result[i] > minVal and result[i] < maxVal:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = ("The power value is %f" % result[i])
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = ("The power value is %f" % result[i])
                
            l_TestStepDescription = ("Check if %s is available" % names[i])
            l_TestStepCriterium = ("The value should be between %fV and %fV" % (minVal, maxVal))
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
            self.testSetup.yav904X8_A3.clearSingle(7+i)
            time.sleep(0.1)
        return l_retval
        
    def X8_PowerTest(self, minVal = 23.0, maxVal = 25.0):
        """
        Check power availability X8
        """
        self.logger.testTitle("Check Power on X8")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        names = ["Power", "INP_Power"]
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        result = []
        for i in range(2):
            self.testSetup.yav904X8_A3.setSingle(1+i)
        
            result.append(self.testSetup.ap.GetDCRes())
            if result[i] > minVal and result[i] < maxVal:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = ("The power value is %f" % result[i])
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = ("The power value is %f" % result[i])
                
            l_TestStepDescription = ("Check if %s is available" % names[i])
            l_TestStepCriterium = ("The value should be between %fV and %fV" % (minVal, maxVal))
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
            self.testSetup.yav904X8_A3.clearSingle(1+i)
            time.sleep(0.1)
        
        return l_retval        
        
    def X7_PowerTest(self, minVal = 23.0, maxVal = 25.0):
        """
        Check power availability X7
        """
        self.logger.testTitle("Check Power on X7")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        names = ["Power", "INP_Power"]
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        result = []
        for i in range(2):
            self.testSetup.yav904X8_A3.setSingle(3+i)
        
            result.append(self.testSetup.ap.GetDCRes())
            if result[i] > minVal and result[i] < maxVal:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
                l_TestStepResult = ("The power value is %f" % result[i])
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                l_TestStepResult = ("The power value is %f" % result[i])
                
            l_TestStepDescription = ("Check if %s is available" % names[i])
            l_TestStepCriterium = ("The value should be between %fV and %fV" % (minVal, maxVal))
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
            self.testSetup.yav904X8_A3.clearSingle(3+i)
            time.sleep(0.1)
        
        return l_retval         
        
    def X6_PowerTest(self, minVal = 23.0, maxVal = 25.0):
        """
        Check power availability X6
        """
        self.logger.testTitle("Check Power on X6")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav904X8.setSingle(25)
        
        result = self.testSetup.ap.GetDCRes()
        
        if result > minVal and result < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The power value is %f" % result)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The power value is %f" % result)
            
        l_TestStepDescription = ("Check if Power is available")
        l_TestStepCriterium = ("The value should be between %fV and %fV" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.yav904X8.clearSingle(25)
        time.sleep(0.1)
        
        return l_retval
        
    def X5_PowerTest(self, minVal = 23.0, maxVal = 25.0):
        """
        Check power availability X5
        """
        self.logger.testTitle("Check Power on X5")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav904X8.setSingle(26)
        
        result = self.testSetup.ap.GetDCRes()
        
        if result > minVal and result < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The power value is %f" % result)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The power value is %f" % result)
            
        l_TestStepDescription = ("Check if Power is available")
        l_TestStepCriterium = ("The value should be between %fV and %fV" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.yav904X8.clearSingle(26)
        time.sleep(0.1)
        return l_retval
        
    def X11_TL34_24V_Detection(self):
        """
        Check power detection TL34
        """
        self.logger.testTitle("Detect Power on Trainlines 3 and 4")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
#         self.ccTn.Close()
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
#         else:
#             self.Reset()
        
        self.ccTn.Connect()
        self.ccTn.Command('Reset')
        self.ccTn.Close()
        
        time.sleep(5)
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav90132.setSingle(7)
        self.testSetup.yav90132.setSingle(8)
        
        self.testSetup.yav90132.setSingle(21)
        self.testSetup.yav90132.setSingle(22)
        
        self.testSetup.powerOnPs2(24,0.3)
        time.sleep(3)
        try:
            resp =  self.ccTn.Read(0x33, 1)
            if resp[0] == "E" or resp[0] == "e":
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                
            l_TestStepResult = ("The response is %s" % resp[0])    
            l_TestStepDescription = ("Check if +24 is detected on trainlines")
            l_TestStepCriterium = ("The response should be E")
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        except:
            self.logger.freeText("ERROR: could not read out register")
            l_retval.append(False)
            l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.powerOffPs2()
        
        self.ccTn.Write(0x42, 0x2000, 1)
        time.sleep(0.1)
        self.ccTn.Write(0x42, 0x0, 1)      
        
        self.testSetup.yav90132.clearSingle(21)
        self.testSetup.yav90132.clearSingle(22)
        
        self.testSetup.powerOnPs2(24,0.3)
        time.sleep(3)
        
        try:
            resp = self.ccTn.Read(0x33, 1)
            if resp[0] == "D" or resp[0] == "d":
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
                
            l_TestStepResult = ("The response is %s" % resp[0])    
            l_TestStepDescription = ("Check if -24 is detected on trainlines")
            l_TestStepCriterium = ("The response should be D")
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        except:
            self.logger.freeText("ERROR: could not read out register")
            l_retval.append(False)
            l_TestStepNumber = l_TestStepNumber + 1
            
        self.testSetup.powerOffPs2()
        
        self.testSetup.yav90132.clearSingle(7)
        self.testSetup.yav90132.clearSingle(8)
        
        self.ccTn.Write(0x42, 0x2000, 1)
        time.sleep(0.1)
        self.ccTn.Write(0x42, 0x0, 1)
        return l_retval
        
    def X11_TL34_24V_Driver(self, minVal = 18.0, maxVal = 22.0):
        """
        Check power availability TL34
        """
        self.logger.testTitle("Put Power on trainlines 3 and 4")
        retval = True
        l_retval = []
        l_TestStepNumber = 1

        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
            
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.testSetup.yav904X8_A3.setSingle(11)
        
        self.ccTn.Write(0x42, 0x1000, 1)
        
        time.sleep(2)
            
        result = self.testSetup.ap.GetDCRes()
        
        if result > minVal and result < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
        
        l_TestStepResult = ("The power value is %f" % result)            
        l_TestStepDescription = ("Check if positive power is available on TL34")
        l_TestStepCriterium = ("The value should be between %fV and %fV" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.ccTn.Write(0x42, 0x2000, 1)
        time.sleep(0.1)
        self.ccTn.Write(0x42, 0x0, 1)
        
        self.ccTn.Write(0x42, 0x4000, 1)
        self.ccTn.Write(0x42, 0x5000, 1)
        
        time.sleep(2)
        
        result = self.testSetup.ap.GetDCRes()
        
        if abs(result) > minVal and abs(result) < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
        
        l_TestStepResult = ("The power value is %f" % result)            
        l_TestStepDescription = ("Check if negative power is available on TL34")
        l_TestStepCriterium = ("The value should be between -%fV and -%fV" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.ccTn.Write(0x42, 0x2000, 1)
        time.sleep(0.1)
        self.ccTn.Write(0x42, 0x0, 1)
        
        self.testSetup.yav904X8_A3.clearSingle(11)
        return l_retval
        
    def X11_TL12_To_TL34_AudioTest(self, inp = 2.0, minVal = 2.0, maxVal = 2.5):
        """
        Test audiopath TL12 --> TL34
        """
        self.logger.testTitle("Send Audio from TL12 to TL34")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        time.sleep(2)
        
        self.testSetup.yav904X8.setSingle(15)
        self.testSetup.yav90132.setSingle(11)
        self.testSetup.yav90132.setSingle(12)
        self.testSetup.yav904X8_A3.setSingle(12)
        
        self.ccTn.Write(0x41, 0x2100, 1)
        self.ccTn.Write(0x42, 0x8, 1)
        self.ccTn.Write(0x70000206, 0x108)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        self.checkDebugMode("Wait untill further notice\n")
        
        result = self.testSetup.ap.GetLvlnGain("Vrms", 5)
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
        
        l_TestStepResult = ("The output value is %fVrms" % result[0])            
        l_TestStepDescription = ("Check if audio from TL12 to TL34 is functional")
        l_TestStepCriterium = ("The output value should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        
        self.testSetup.ap.turnOfGenerator()
        
        self.testSetup.yav904X8.clearSingle(15)
        self.testSetup.yav90132.clearSingle(11)
        self.testSetup.yav90132.clearSingle(12)
        self.testSetup.yav904X8_A3.clearSingle(12)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x42, 0x0, 1)
        self.ccTn.Write(0x70000206, 0x0)
        
        return l_retval
        
    def X11_TL34_To_TL12_AudioTest(self, inp = 2.0, minVal = 2.0, maxVal = 2.5):
        """
        Test audiopath TL34 --> TL12
        """
        self.logger.testTitle("Send Audio from TL34 to TL12")
        retval = True
        l_retval = []
        l_TestStepNumber = 1

        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()

        time.sleep(2)
        
        self.testSetup.yav904X8.setSingle(16)
        self.testSetup.yav90132.setSingle(9)
        self.testSetup.yav90132.setSingle(10)
        self.testSetup.yav904X8_A3.setSingle(10)
        
        self.ccTn.Write(0x41, 0x0500, 1)
        self.ccTn.Write(0x42, 0x8, 1)
        self.ccTn.Write(0x70000206, 0x009)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        self.checkDebugMode("Wait untill further notice\n")        
        
        result = self.testSetup.ap.GetLvlnGain("Vrms", 5)
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
        
        l_TestStepResult = ("The output value is %fVrms" % result[0])            
        l_TestStepDescription = ("Check if audio from TL34 to TL12 is functional")
        l_TestStepCriterium = ("The output value should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.testSetup.yav904X8.clearSingle(16)
        self.testSetup.yav90132.clearSingle(9)
        self.testSetup.yav90132.clearSingle(10)
        self.testSetup.yav904X8_A3.clearSingle(10)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x42, 0x0, 1)
        self.ccTn.Write(0x70000206, 0x0)
        
        return l_retval
        
    def Chassis_connectionTest(self, minVal = 4.5, maxVal = 5.5):
        """
        Test all chassis pins
        """
        self.logger.testTitle("Check if chassis pins are connected")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        names = ["CHASSIS_16Z",
                 "CHASSIS_24Z",
                 "CHASSIS_28Z",
                 "CHASSIS_32Z",
                 "CHASSIS_X7",
                 "CHASSIS_X9",
                 "CHASSIS_X6",
                 "CHASSIS_22Z",
                 "CHASSIS_26Z",
                 "CHASSIS_30Z",
                 "CHASSIS_X8",
                 "CHASSIS_X10",
                 "CHASSIS_X5"]
        
        if self.pwrState == PWR_STATE.ON:
            self.ccTn.EnterDegradeMode
            self.ccTn.Close()
            self.FullPowerOff()
            
        self.PowerOnChassis()
        
        cnt = 0
        for i in range(2):
            self.testSetup.yav904X8_A3.setSingle(13+i)
            for j in range(7):
                if i != 1  or j != 6:
                    self.testSetup.yav904X8_A3.setSingle(25+j)
                    result = self.testSetup.ap.GetDCRes()
                    if result > minVal and result < maxVal:
                        l_TestStepConclusion = "PASS"
                        l_retval.append(True)
                    else:
                        l_TestStepConclusion = "FAIL"
                        l_retval.append(False)
                    
                    l_TestStepResult = ("The measured voltage is %fV" % result)            
                    l_TestStepDescription = ("Check if %s pin is connected to chassis" % names[cnt])
                    l_TestStepCriterium = ("The measured voltage should be between %fV and %fV" % (minVal, maxVal))
                    
                    self.logger.structured(l_TestStepNumber, 
                                           l_TestStepDescription, 
                                           l_TestStepCriterium, 
                                           l_TestStepResult, 
                                           l_retval[l_TestStepNumber-1])
                    l_TestStepNumber = l_TestStepNumber + 1
                    
                    self.testSetup.yav904X8_A3.clearSingle(25+j)
                    time.sleep(0.2)
                    cnt += 1
            self.testSetup.yav904X8_A3.clearSingle(13+i)
            time.sleep(0.2)
        
        self.PowerOffChassis()
        
        return l_retval
    
    def X11_TL_Shield_Test(self, minVal = 0.0, maxVal = 0.1):
        """
        Test Trainline shield
        """
        self.logger.testTitle("Check if chassis pins from trainlines are connected")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        if self.pwrState == PWR_STATE.ON:
            self.ccTn.EnterDegradeMode
            self.ccTn.Close()
            self.FullPowerOff()
            
        inp = 5
        freq = 1000
        
        results = []
        
        for i in range(2):
            self.testSetup.yav90132_B3.setSingle(27+i)
            self.testSetup.yav90132_B3.setSingle(29+i)
            self.testSetup.yav904X8_A3.setSingle(15+i)
        
            self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms", freq)
            result = self.testSetup.ap.GetLvlnGain("Vrms", 5)
            results.append(result[0])
            if results[i] > minVal and results[i] < maxVal:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
            
            l_TestStepResult = ("The measured voltage is %fV" % results[0])            
            l_TestStepDescription = ("Check if TL%d%d shield is connected to chassis" % (((i*2)+1),((i+1)*2)))
            l_TestStepCriterium = ("The voltage over the connection should be between %fV and %fV" % (minVal, maxVal))
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
            self.testSetup.ap.turnOfGenerator()
            
            self.testSetup.yav90132_B3.clearSingle(27+i)
            self.testSetup.yav90132_B3.clearSingle(29+i)
            self.testSetup.yav904X8_A3.clearSingle(15+i)
        
            time.sleep(0.1)
        
        return l_retval
        
    def RS485_Termination_Test(self, minVal = 0.3, maxVal = 0.4):
        """
        Test RS485 termination
        """
        self.logger.testTitle("Check if RS485 bus is correctly terminated")
        retval = True
        l_retval = []
        l_TestStepNumber = 1        
        
        if self.pwrState == PWR_STATE.ON:
            self.ccTn.EnterDegradeMode
            self.ccTn.Close()
            self.FullPowerOff()
            
        self.PowerOnChassis()
        result = []
        
        self.testSetup.yav90132.setSingle(16)
        self.testSetup.yav90132.setSingle(15)
        
        for i in range (6):
            time.sleep(1)
            self.testSetup.yav904X8_A3.setSingle(17+i)
            result.append(self.testSetup.multiMeter.readCurrentVoltage("PXI1Slot4/ai0"))
            
            if result[i] > minVal and result[i] < maxVal:
                l_TestStepConclusion = "PASS"
                l_retval.append(True)
            else:
                l_TestStepConclusion = "FAIL"
                l_retval.append(False)
            
            l_TestStepResult = ("The measured voltage is %fV" % result[i])            
            l_TestStepDescription = ("Check if peripheral %d (connector X%d) is correctly terminated" % ((i+1),(10-i)))
            l_TestStepCriterium = ("The voltage over the termination should be between %fV and %fV" % (minVal, maxVal))
            
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
            
            self.testSetup.yav904X8_A3.clearSingle(17+i)
            time.sleep(0.1)
            
        self.testSetup.yav90132.clearSingle(16)
        self.testSetup.yav90132.clearSingle(15)
            
        self.PowerOffChassis()
        
        return l_retval
    
    def ethernetPortTest(self, Connector, cc_art_nr = '33.92.6775'):
        """
        Test ethernetports
        """
        self.logger.testTitle("Test Ethernet port %s" % Connector)
        
#         M12_con = ['X1', 'X3', 'X4']
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if self.pwrState == PWR_STATE.ON:
            if self.servicePort:
                self.ccTn.EnterDegradeMode
                self.ccTn.Close()
            self.Reset()
        else:
            self.FullPowerOn()
        self.closeservicePort()
        
#         self.ccTn.Connect()
#         self.ccTn.EnterDegradeMode()
#         self.ccTn.Close()
        
#         for i in range(3):
        self.openRouterPort(tlv_cc_pmdb[cc_art_nr]['Ports'][Connector]['cisco_intf'])
        try:        
            self.checkDebugMode("Wait to ping")
            #time.sleep(2)
            res = ping_to_ip(tlv_cc_pmdb[cc_art_nr]['Ports'][Connector]['test_ip'], 
                             on_time, ping_count, ping_packet_size, ping_timeout, ping_udp, wait_timer)
               
            if res['ret_code'] == 0:
                l_retval.append(True)
                l_TestStepResult = ("Connector %s does respond" % Connector)
            else:
                l_retval.append(False)
                l_TestStepResult = ("Connector %s does NOT respond" % Connector)    
                
#             res = os.system("ping -n 1 " + tlv_cc_pmdb[cc_art_nr]['Ports'][Connector]['test_ip'])
#             if res == 0:
#                 l_retval.append(True)
#                 l_TestStepResult = ("Connector %s does respond" % Connector)
#             else:
#                 l_retval.append(False)
#                 l_TestStepResult = ("Connector %s does NOT respond" % Connector)               
                          
            l_TestStepDescription = ("Test if connector %s is working" % Connector)
            l_TestStepCriterium = ("Connector %s should respond to ping command" % Connector)
              
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        except Exception:
            self.logger.freeText("ERROR: Check if python is executed as administrator")
            return False
        
        self.closeRouterPort(tlv_cc_pmdb[cc_art_nr]['Ports'][Connector]['cisco_intf'])
        
        self.openservicePort()
        return l_retval
    
    def RS485_Com_test(self, Connector):
        self.logger.testTitle("RS485 communication test connecor %s" % Connector)
        Connectors = ['X10', 'X9', 'X8', 'X7', 'X5', 'X6']
        relay = None
        for i in range(6):
            if Connector == Connectors[i]:
                relay = 17 + i
                
        if relay ==  None:
            self.logger.freeText("ERROR: No connector match")
            return False
        
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        RS485_in = RS485.RS485('COM3')
        dataOut = RS485_in.getSendData()
        checkDataIn = RS485_in.getReceivedData()
         
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
            
        self.checkDebugMode("Wait to open telnet")
        self.ccTn.Connect()
        
        self.ccTn.ExitDegradeMode()
             
        self.testSetup.yav904X8_A3.setSingle(23)
#         for i in range(6):
        RS485_in.emptyBuffer()
        
        self.testSetup.yav904X8_A3.setSingle(relay)
        self.ccTn.Command('vcsport testmode 1')
        
        datain = RS485_in.rx(5)
        
        RS485_in.tx(dataOut)
        time.sleep(0.2)
        
        #read out received data
        datain = RS485_in.rx(17)
        if datain[2] == 0x31:
            datain = datain[0:12]
        else:
            datain = datain[5:17]
#             datain = RS485_in.rx(12)
        
        self.ccTn.Command('vcsport testmode 0')
        
        if datain == checkDataIn:
            l_retval.append(True)
        else:
            l_retval.append(False)
            
        l_TestStepResult = "Response is: " + ('0x' + ' 0x'.join('%02x' % byte for byte in datain))
        
        l_TestStepDescription = ("Test if RS485 on connector %s is working" % Connector)
        l_TestStepCriterium = "Response should be :" + ('0x' + ' 0x'.join('%02x' % byte for byte in checkDataIn))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1                
        
        self.testSetup.yav904X8_A3.clearSingle(relay)
#
        self.testSetup.yav904X8_A3.clearSingle(23)
        
        RS485_in.closePort()
        
        return l_retval
    
    def updateApp(self, file, AppVersion):
        self.logger.testTitle("Update application")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
         
        if not self.servicePort:
            self.openservicePort()
         
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        try:
            self.ccTn.Connect()
        except Exception:
            return False
        
        self.ccTn.Command("main unlock")
        
        self.ccTn.Close()
        
        filesize = os.path.getsize(file)
        
        tftp = tftpy.TftpClient('80.0.0.2', 69)
        
        self.logger.freeText("Start uploading sw (This may take a while)")
        tftp.upload('newapp_'+str(filesize), file)
        
        time.sleep(30)
        print "DONE"
        self.logger.freeText("Uploading DONE")
        
        self.Reset()
#         raw_input("Press enter after repowering")
        
        try:
            self.ccTn.Connect()
        except Exception:
            return False
        
        vers = self.ccTn.ReadVersions()
        vers = StringIO.StringIO(vers)
        vers.readline()
        vers = vers.readline()
            
        if vers[0:len(vers)-2] == AppVersion:
            l_retval.append(True)
        else:
            l_retval.append(False)
        l_TestStepDescription = ("Update to final app")
        l_TestStepCriterium = "Should be " + AppVersion
        l_TestStepResult = vers[0:len(vers)-2]
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        print l_TestStepResult
        
        self.ccTn.Close()
        cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf='GE3', intf_state='shutdown')
        self.closeRouterTelnet()
        
        return l_retval
    
    def makeReadyForTest(self):
        self.logger.testTitle("Put on test software for test")
        ShouldBeVers = "33.96.7611: 1.05.08"
        retval = True
        l_retval = []
        l_TestStepNumber = 1
         
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        try:
            self.ccTn.Connect()
            
            self.ccTn.Command("main unlock")
            
            self.ccTn.Close()
            
            filesize = os.path.getsize('..\\33.98.0095\\pr_7611.bin')
            
            tftp = tftpy.TftpClient('80.0.0.2', 69)
            
            self.logger.freeText("Start uploading sw (This may take a while)")
            tftp.upload('newapp_'+str(filesize), '..\\33.98.0095\\pr_7611.bin')
            
            time.sleep(30)
            
            self.logger.freeText("Uploading DONE")
            
        except Exception as e:
            return False
        
        self.Reset()
        
        if self.ccTn.Connect() == False:
            return False
        
        vers = self.ccTn.ReadVersions()
        vers = StringIO.StringIO(vers)
        vers.readline()
        vers = vers.readline()
            
        if vers[0:len(vers)-2] == ShouldBeVers:
            l_retval.append(True)
        else:
            l_retval.append(False)
               
        l_TestStepDescription = ("Check if cc is ready for test")
        l_TestStepCriterium = "Should be " + ShouldBeVers
        l_TestStepResult = vers[0:len(vers)-2]
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        return l_retval
        
        
    def VisualCheck(self):
        self.logger.testTitle("Visual check")
        
    def test(self):
        if not self.ccTn.connected:
            print "open telnet"
            self.ccTn.Connect()
            
        if self.ccTn.degraded:
            
            print "exit degrade mode"
            self.ccTn.ExitDegradeMode()
        
class test33927410LRU(test33926775LRU):    
    def __init__(self, voltage, current, 
                 ipAddress = "80.0.0.2",
                 login = "",
                 password = ""):
        
        self.testSetup = testsystem_setup.TestSetup33988041()
        self.logger = Logger.testLogger()
        
        self.BAT_VOLTAGE = voltage/2
        self.LIMIT_CURRENT = current
        self.ip = ipAddress
        self.ccTn = cc_telnet.cc_telnet(ipAddress, port = 23,
                                        login = login,
                                        password = password)
        
        self.time_out = 20
        
        self.routerTelnetSetup()
        self.servicePort = False
        
        self.debug_mode = False
        self.debug_mode_next = False
        pub.subscribe(self.enterDebugMode, 'ENTER_DEBUG_MODE')
        pub.subscribe(self.goToNextBreakpoint, 'NEXT_BREAKPOINT')
        
    def VersionCheck(self, App, IfFpga, IpFpga, DSP, Jingles):
        """
        Check the versions of the CC
        """
        S01Versions = []
        S01Versions.append(App)
        S01Versions.append(IfFpga)
        S01Versions.append(IpFpga)
        S01Versions.append(DSP)
        S01Versions.append(Jingles)
        
        sw = ["app", "fpga if board", "fpga ip board", "DSP", "Jingles"]
        
        self.logger.testTitle("Check the SW and SW versions")
        
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        
        versions = self.ccTn.ReadVersions()
        Versions = StringIO.StringIO(versions)
        
        vers = Versions.readline()
        print "Version should be " + S01Versions[0] + "    Version should be " + vers[0:len(vers)-5]
        if vers[0:len(vers)-5] == S01Versions[0]:
            print "PASS"
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
        else:
            print "FAIL"
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            
        l_TestStepDescription = ("Check SW and SW version for %s" % sw[0])
        l_TestStepCriterium = ("Should be %s" % S01Versions[0])
        l_TestStepResult = vers[0:len(vers)-5]
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        Versions.readline()
        
        for i in range(4):
            vers = Versions.readline()
            print "Version should be " + S01Versions[i+1] + "    Version should be " + vers[0:len(vers)-5]
            
            if i == 2:
                if vers[0:len(vers)-24] == S01Versions[i+1]:
                    print "PASS"
                    l_TestStepConclusion = "PASS"
                    l_retval.append(True)
                else:
                    print "FAIL"
                    l_TestStepConclusion = "FAIL"
                    l_retval.append(False)
                    
                l_TestStepDescription = ("Check SW and SW version for %s" % sw[i+1])
                l_TestStepCriterium = ("Should be %s" % S01Versions[i+1])
                l_TestStepResult = vers[0:len(vers)-24]
            else:
                if vers[0:len(vers)-5] == S01Versions[i+1]:
                    print "PASS"
                    l_TestStepConclusion = "PASS"
                    l_retval.append(True)
                else:
                    print "FAIL"
                    l_TestStepConclusion = "FAIL"
                    l_retval.append(False)
                    
                l_TestStepDescription = ("Check SW and SW version for %s" % sw[i+1])
                l_TestStepCriterium = ("Should be %s" % S01Versions[i+1])
                l_TestStepResult = vers[0:len(vers)-5]
            self.logger.structured(l_TestStepNumber, 
                                   l_TestStepDescription, 
                                   l_TestStepCriterium, 
                                   l_TestStepResult, 
                                   l_retval[l_TestStepNumber-1])
            l_TestStepNumber = l_TestStepNumber + 1
        
        return l_retval
        
    def updateApp(self, file, AppVersion):
        self.logger.testTitle("Update application")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
         
        if not self.servicePort:
            self.openservicePort()
         
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        
        self.ccTn.Command("main unlock")
        
        self.ccTn.Close()
        
        filesize = os.path.getsize(file)
        
        tftp = tftpy.TftpClient('80.0.0.2', 69)
        
        self.logger.freeText("Start uploading sw (This may take a while)")
        tftp.upload('newapp_'+str(filesize), file)
        
        time.sleep(30)
        print "DONE"
        self.logger.freeText("Uploading DONE")
        
        self.Reset()
#         raw_input("Press enter after repowering")
        
        self.ccTn.Connect()
        
        vers = self.ccTn.ReadVersions()
        vers = StringIO.StringIO(vers)
        vers = vers.readline()
            
        if vers[0:len(vers)-5] == AppVersion:
            l_retval.append(True)
        else:
            l_retval.append(False)
        l_TestStepDescription = ("Update to final app")
        l_TestStepCriterium = "Should be " + AppVersion
        l_TestStepResult = vers[0:len(vers)-5]
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        print l_TestStepResult
        
        self.ccTn.Close()
        cisco_sf300_connector.cisco_sf300_telnet_intf_state(self.netsw_telnet, intf='GE3', intf_state='shutdown')
        self.closeRouterTelnet()
        
        return l_retval
        
    def X11_LS1_Audio_Test(self, inp = 5.0, minVal = 2.4, maxVal = 2.8):
        """
        Test audio path of LS1
        """
        self.logger.testTitle("Check Audio of LS1 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()

        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(1)
        self.testSetup.yav90132_B3.setSingle(2)
        self.testSetup.yav90132_B3.setSingle(3)
        self.testSetup.yav90132_B3.setSingle(4)
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(27)
        
        self.ccTn.Write(0x70000206, 0x612)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS1")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x600)     
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(1)
        self.testSetup.yav90132_B3.clearSingle(2)
        self.testSetup.yav90132_B3.clearSingle(3)
        self.testSetup.yav90132_B3.clearSingle(4)
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(27)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval
        
    def X11_LS2_Audio_Test(self, inp = 5.0, minVal = 2.4, maxVal = 2.8):
        """
        Test audio path of LS2
        """
        self.logger.testTitle("Check Audio of LS2 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(5)
        self.testSetup.yav90132_B3.setSingle(6)
        self.testSetup.yav90132_B3.setSingle(7)
        self.testSetup.yav90132_B3.setSingle(8)
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(28)
        
        self.ccTn.Write(0x70000206, 0x712)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS2")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x700)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(5)
        self.testSetup.yav90132_B3.clearSingle(6)
        self.testSetup.yav90132_B3.clearSingle(7)
        self.testSetup.yav90132_B3.clearSingle(8)
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(28)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval        

        
    def X11_LS3_Audio_Test(self, inp = 5.0, minVal = 2.4, maxVal = 2.8):
        """
        Test audio path of LS3
        """
        self.logger.testTitle("Check Audio of LS3 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(9)
        self.testSetup.yav90132_B3.setSingle(10)
        self.testSetup.yav90132_B3.setSingle(11)
        self.testSetup.yav90132_B3.setSingle(12)
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(29)
        
        self.ccTn.Write(0x70000206, 0xA12)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS3")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0xA00)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(9)
        self.testSetup.yav90132_B3.clearSingle(10)
        self.testSetup.yav90132_B3.clearSingle(11)
        self.testSetup.yav90132_B3.clearSingle(12)
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(29)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        return l_retval    
        
    def X11_LS4_Audio_Test(self, inp = 5.0, minVal = 2.4, maxVal = 2.8):
        """
        Test audio path of LS4
        """
        self.logger.testTitle("Check Audio of LS4 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(13)
        self.testSetup.yav90132_B3.setSingle(14)
        self.testSetup.yav90132_B3.setSingle(15)
        self.testSetup.yav90132_B3.setSingle(16)
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(30)
        
        self.ccTn.Write(0x70000206, 0x812)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        self.checkDebugMode("Wait until level is up")
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
            
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS4")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x800)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(13)
        self.testSetup.yav90132_B3.clearSingle(14)
        self.testSetup.yav90132_B3.clearSingle(15)
        self.testSetup.yav90132_B3.clearSingle(16)
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(30)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval
        
    def X11_LS5_Audio_Test(self, inp = 5.0, minVal = 2.4, maxVal = 2.8):
        """
        Test audio path of LS5
        """
        self.logger.testTitle("Check Audio of LS5 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(17)
        self.testSetup.yav90132_B3.setSingle(18)
        self.testSetup.yav90132_B3.setSingle(19)
        self.testSetup.yav90132_B3.setSingle(20)
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(31)
        
        self.ccTn.Write(0x70000206, 0x912)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS5")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0x900)
        time.sleep(0.1)        
        
        self.testSetup.yav90132_B3.clearSingle(17)
        self.testSetup.yav90132_B3.clearSingle(18)
        self.testSetup.yav90132_B3.clearSingle(19)
        self.testSetup.yav90132_B3.clearSingle(20)
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(31)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval         
        
    def X11_LS6_Audio_Test(self, inp = 5.0, minVal = 2.4, maxVal = 2.8):
        """
        Test audio path of LS6
        """
        self.logger.testTitle("Check Audio of LS6 X11")
        retval = True
        l_retval = []
        l_TestStepNumber = 1
        
        if not self.servicePort:
            self.openservicePort()
        
        if self.pwrState == PWR_STATE.OFF:
            self.FullPowerOn()
        
        self.ccTn.Connect()
        self.ccTn.ExitDegradeMode()
        
        self.ccTn.Write(0x41, 0x100, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        self.testSetup.yav90132_B3.setSingle(21)
        self.testSetup.yav90132_B3.setSingle(22)
        self.testSetup.yav90132_B3.setSingle(23)
        self.testSetup.yav90132_B3.setSingle(24)
        self.testSetup.yav90132_B3.setSingle(25)
        self.testSetup.yav90132_B3.setSingle(26)
        
        self.testSetup.yav904X8.setSingle(32)
        
        self.ccTn.Write(0x70000206, 0xB12)
        self.ccTn.Command("setVolumeAll 0")
        time.sleep(1)
        self.testSetup.ap.SetLvlnGainGen(0, inp, "Vrms")
        for i in range(20):
            result = self.testSetup.ap.GetLvlnGain("Vrms", 1)
            if result[0] > minVal and result[0] < maxVal:
                break
        
        if result[0] > minVal and result[0] < maxVal:
            l_TestStepConclusion = "PASS"
            l_retval.append(True)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
        else:
            l_TestStepConclusion = "FAIL"
            l_retval.append(False)
            l_TestStepResult = ("The output value is %fVrms" % result[0])
            
        l_TestStepDescription = ("Check the output value LS6")
        l_TestStepCriterium = ("The output should be between %fVrms and %fVrms" % (minVal, maxVal))
        
        self.logger.structured(l_TestStepNumber, 
                               l_TestStepDescription, 
                               l_TestStepCriterium, 
                               l_TestStepResult, 
                               l_retval[l_TestStepNumber-1])
        l_TestStepNumber = l_TestStepNumber + 1
        
        self.testSetup.ap.turnOfGenerator()
        
        self.ccTn.Write(0x70000206, 0xB00)
        time.sleep(0.1)
        
        self.testSetup.yav90132_B3.clearSingle(21)
        self.testSetup.yav90132_B3.clearSingle(22)
        self.testSetup.yav90132_B3.clearSingle(23)
        self.testSetup.yav90132_B3.clearSingle(24)
        self.testSetup.yav90132_B3.clearSingle(25)
        self.testSetup.yav90132_B3.clearSingle(26)
         
        self.testSetup.yav904X8.clearSingle(32)
        
        self.ccTn.Write(0x41, 0x0, 1)
        self.ccTn.Write(0x43, 0x0, 1)
        
        return l_retval
    
#     def X11_TL_Shield_Test(self, minVal = 0.0, maxVal = 1.0):
        
if __name__=='__main__':
    prob = test33927410LRU(110,5)
    prob.FullPowerOn()
    raw_input("Press enter to continue")
    prob.FullPowerOff()
    #prob.updateApp(file = "\\\\fileserver\\fileserver\\R&D\\Ontwikkelingen\\33.96.7980\\F.studie\\4.Software\\RLS_33967980_1-01\\pr_7980.bin", AppVersion = '33.96.7980: 1.01')
    
    
