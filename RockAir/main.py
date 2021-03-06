import pycom
import machine
from network import LoRa
from network import WLAN
from machine import SD
from machine import WDT
from machine import UART
from microWebSrv import MicroWebSrv
from robust import MQTTClient
import socket
import time
import utime
import _thread
import struct
import gc
import ujson
import os
import ubinascii
import uctypes
import crc16
from tracker import tracker
from rgb import RGBLED

##################################################
## configuration
##################################################

swVer = '0.2' # software version
hwVer = '0.1' # hardware version
my_ID = 'BSE1' # unique id of this unit - 4 char string
known_nets = { 'Galilean': {'pwd': 'ijemedoo'}, 'lerdy': {'pwd': 'lerdy0519'} } # known WLAN networks to attempt connection to
mqttSendInterval = 1 * 30 * 1000  # Send data to TracPlus every (1* 30 * 1000)usec = 30 seconds
trackerSendInterval = 1 * 60 * 1000  # Send data to TracPlus every (2 * 60 * 1000)usec = 2 minutes
WDtimeout = int(25 * 1000) # use watchdog timer to reset the board if it does not update reguarly (25 seconds)
log_filename = "/sd/log.csv" # file name for log file on SD card
ledInterval = 1000 # update LED every 1000msec
ledLightness = 5 # brightness value for indicator LED
receiveInterval = 2 # recive data every 2 seconds
staleGPStime = 10 # after 10 seconds consider the GPS stale
TZ_offset_secs = 10*60*60  # AEST is +10 hours offset from UTC
ntp_source = 'pool.ntp.org' # URL for network time protocol source
napTime = 5 # number of milli seconds to nap to allow other things to happening
FixTimeout = 1000 * 30  # 30 seconds in ms
use_MQTT = True # if true and if internet available send data to MQTT server
use_WebServer = True # if true and if network available fire up the local web server

##################################################
## Variables
##################################################

# instantiate libraries
wdt = WDT(timeout=WDtimeout) # enable a Watchdog timer with a specified timeou
led = RGBLED(10)  # start the status LED library
rtc = machine.RTC() # start the real time clock library

# initialise variables
GPSFix = False
lastFix = utime.time() - FixTimeout
last_recv_time = utime.time() - receiveInterval
last_msg_send = utime.time() - trackerSendInterval
last_mqtt_send = utime.time() - mqttSendInterval
msgReceived = False
geoJSON = {}
deltaLat = 0
deltaLon = 0
msgCount = 0
crcErrorCount = 0
last_LED_check = 0
LED_count = 0
network_OK = False
internet_OK = False
tracker_OK = False

##################################################
## Functions
##################################################

def update_LED():
# continuously update the status of the unit via the onboard LED
    global GPSFix
    global msgReceived
    global ledInterval
    global last_LED_check
    global LED_count
    ledColour = 0x000000

    if(utime.ticks_ms() - last_LED_check > ledInterval/100):
        last_LED_check = utime.ticks_ms()
        LED_count += 1
        if (LED_count<90):
            if GPSFix:
                # GPS OK so set LED to Green
                ledHue= led.GREEN
            else:
                # GPS BAD so set LED to RED
                ledHue= led.RED
            if GPSFix and utime.time() - lastFix > staleGPStime:
                # No GPS fix for staleGPStime seconds so set LED to Orange
                ledHue= led.ORANGE
            if msgReceived == True:
                # Just sent a message so set the LED to Magenta
                ledHue= led.MAGENTA
                #reset the msg flag as we have actioned it
                msgReceived = False
            # set the LED colour with variable lightness based on LED_count
            led.hl(ledHue,LED_count)
        else:
            # blink LED off for 10% of the time
            led.off()
            LED_count = 0

def sendMqttMsg():
# periodically send data to MQTT server
    global GPSFix
    global theData
    global mqttSendInterval
    global last_mqtt_send

    if(utime.ticks_ms() - last_mqtt_send > mqttSendInterval):
        last_mqtt_send = utime.ticks_ms()
        if GPSFix > 0:
            # GPS OK so send a message
            print ("GPS OK - send MQTT message")
            # Free up memory by garbage collecting
            gc.collect()
            try:
                # create message to send
                theMsg = theData["uid"] + ',' + str(theData["fix"]) + ',' + str(theData["lat"]) + ',' + str(theData["lon"]) + ',' + str(theData["gdt"]) + ',' + str(stats.rssi) + ',' + str(RockAir._latitude[3]) + ',' + str(RockAir._longitude[3])
                # send data to MQTT server
                mqtt.publish(topic="agmatthews/feeds/LORAtest", msg=theMsg)
            except Exception as e:
                print('   Data Error - No send via MQTT')
                print(theData)
        else:
            print('No FIX - No send via MQTT')

def mqtt_callback(topic, msg):
    # this subroutine would process any message that comes back from MQTT server
    print(msg)

def sendTrackerMsg():
# periodically send data to TracPlus via RockAir
    global GPSFix
    global theData
    global trackerSendInterval
    global last_msg_send
    global RockAir

    if(utime.ticks_ms() - last_msg_send > trackerSendInterval):
        last_msg_send = utime.ticks_ms()
        if GPSFix > 0:
            # GPS OK so send a message
            print ("GPS OK - send Tracker message")
            # Free up memory by garbage collecting
            gc.collect()
            if (RockAir.valid):
                tracker_OK = True
                print('   Tracker OK: ',RockAir._latitude[3],RockAir._longitude[3])
                # create a dictionary object to hold remote message data
                try:
                    messageData = {}
                    messageData["EVT"] = 'REMOTE'
                    messageData["ID"] = theData["uid"]
                    messageData["LATD"] = deltaLat #lat delta
                    messageData["LOND"] = deltaLon #lon delta
                    messageData["SPD"] = "{:.0f}".format(theData["spd"])
                    messageData["COG"] = "{:.0f}".format(theData["cog"])
                    messageData["ALT"] = "{:.0f}".format(theData["alt"])
                    messageData["BAT"] = "{:.2f}".format(theData["bat"])
                    # encode the message data as JSON without wasted spaces
                    encodedData = ujson.dumps(messageData).replace(" ", "")
                    # send the remote message
                    RockAir.sendMessage(encodedData)
                except Exception as e:
                    print('   Data Error - No send via Tracker')
                    print(theData)
            else:
                print('   No valid Tracker')
        else:
            print('No FIX - No send via Tracker')

def WWW_routes():
    global geoJSON
    def _geojson(client, response):
        response.WriteResponseJSONOk(geoJSON)
    return [('/gps.json', 'GET', _geojson)]

##################################################
## MAIN loop
##################################################

# print software version info
print (os.uname())

print ('Starting BASE (LoRaTracker)')
print ('   UnitID: ' + str(my_ID))

print ("Starting LED")
led.h(led.BLUE)

print ("Starting Network")
wlan = WLAN()
wlan.mode(WLAN.STA)
# scan for available networks
available_nets = wlan.scan()
for net in available_nets:
    print('   Found SSID: '+ net.ssid)
nets = frozenset([e.ssid for e in available_nets])
# match available nets with known nets
known_nets_names = frozenset([key for key in known_nets])
net_to_use = list(nets & known_nets_names)
# try and use the first matching network
try:
    net_to_use = net_to_use[0]
    net_properties = known_nets[net_to_use]
    pwd = net_properties['pwd']
    sec = [e.sec for e in available_nets if e.ssid == net_to_use][0]
    print('   Connecting to: ' + net_to_use)
    if 'wlan_config' in net_properties:
        wlan.ifconfig(config=net_properties['wlan_config'])
    wlan.connect(net_to_use, auth=(sec, pwd), timeout=5000)
    while not wlan.isconnected():
        # idle power while waiting
        machine.idle()
    print('   Connected.')
    print('   IP address: ' + wlan.ifconfig()[0])
    network_OK = True
    internet_OK = True

except Exception as e:
    print('   Cant connect to known networks')
    print('   Entering AP mode')
    wlan.init(mode=WLAN.AP, ssid='GPSnode', channel=6, antenna=WLAN.INT_ANT)
    network_OK = True

print('Starting Clocks')
if network_OK:
    print('   Syncing RTC to '+ ntp_source)
    rtc.ntp_sync(ntp_source)
    utime.sleep_ms(1500)
    print('   RTC Time :', rtc.now())
utime.timezone(TZ_offset_secs)
print('   Local Time:', utime.localtime())


print ("Starting Tracker")
print ("   Open Serial Port")
RockAir = tracker(location_formatting='dd')
# get the current location from the tracker
RockAir.getGPS()
if (RockAir.valid):
# check we got some data
    tracker_OK = True
    print('   Tracker OK: ',RockAir._latitude[3],RockAir._longitude[3])
    # create a dictionary object to hold startup message data
    startupData = {}
    startupData["EVT"] = 'STARTUP'
    startupData["ID"] = my_ID
    startupData["SW"] = swVer
    startupData["HW"] = hwVer
    # encode the message data as JSON without spaces
    encodedData = ujson.dumps(startupData).replace(" ", "")
    # send the startup message
    RockAir.sendMessage(encodedData)
else:
    print('   Tracker ERROR')
    tracker_OK = False

print ("Starting SD Card")
sd = SD()
os.mount(sd, '/sd')
maxIndex = 0
# loop through all the files on the SD card
for f in os.listdir('/sd'):
    #look for GPSlognnnn files
    if f[:6]=='GPSlog':
        try:
            # extract the number from the GPSlognnnn filename
            index = int(f[6:].split(".")[0])
        except ValueError:
            index = 0
        # if this is the highest numbered file then record it
        if index > maxIndex:
            maxIndex = index
if maxIndex>9999:
    print ('   SD card file name error - too many files')
# create a new filename one number higher that the highest on theSD card
log_filename = '/sd/GPSlog{:04d}.csv'.format(maxIndex+1)
print('   Logfile: ' + log_filename)
# start new log file with headers
with open(log_filename, 'a') as Log_file:
    Log_file.write(str(rtc.now()) + '\n')
    Log_file.write('remote_ID,GPSFix,latitude,longitude,voltage,rssi\n')

if use_WebServer and network_OK:
    print ("Starting Webserver")
    routes = WWW_routes()
    mws = MicroWebSrv(routeHandlers=routes, webPath="/sd") # TCP port 80 and files in /sd
    gc.collect()
    mws.Start()
    gc.collect()

if use_MQTT and internet_OK:
    print ('Starting MQTT')
    mqtt = MQTTClient(my_ID, "io.adafruit.com",user="agmatthews", password="d9ee3d9d1d5a4f3b860d96beaa9d3413", port=1883)
    mqtt.set_callback(mqtt_callback)
    mqtt.connect()
    mqtt.subscribe(topic="agmatthews/feeds/LORAtest")

print ("Starting Lora")
lora = LoRa(mode=LoRa.LORA, region=LoRa.AU915)
s = socket.socket(socket.AF_LORA, socket.SOCK_RAW)
s.setblocking(False)

print ("Waiting for data")

while True:
    # feed the watch dog timer
    wdt.feed()
    # update the status LED
    update_LED()
    # Free up memory by garbage collecting
    gc.collect()
    # periodically send message via Tracker
    sendTrackerMsg()
    # periodically send message via MQTT
    if use_MQTT and internet_OK:
        sendMqttMsg()
    # if we havent had a fix recently then time out the most recent fix
    if utime.time() - lastFix > FixTimeout:
        GPSFix = False
    # if it is time to check for a message then check for it
    if(utime.time() > last_recv_time + receiveInterval):
        # get some data from the LoRa buffer
        databytes = s.recv(256)
        stats = lora.stats()
        if len(databytes)>=40:
            print (' ')
            print ("Message Received")
            # record the time of this fix in local seconds
            last_recv_time = utime.time()
            theData = {}
            theData["fix"] = False
            msgReceived = True
            msgCount += 1
            # check the crc on the recived message
            if crc16.checkcrc(databytes):
                # CRC is OK  - process message
                theData = ujson.loads(databytes[6:].decode())
                # check GPS data in the recived data
                if theData["fix"] and theData["lon"]!=0:
                    # GPS is good - process message
                    GPSFix = True
                    # record the time of this fix in local seconds
                    lastFix = utime.time()
                    # make a geoJSON package of the recived data
                    geoJSON = {"geometry": {"type": "Point", "coordinates": [str(theData["lon"]),str(theData["lat"])]}, "type": "Feature", "properties": {"Unit_ID": theData["uid"], "altitude": str(theData["alt"]), "speed": str(theData["spd"]), "course": str(theData["cog"]), "battery": str(theData["bat"]), "RSSI": str(stats.rssi), "datetime": str(theData["gdt"])}} #, "RockAir_Lat": str(lat_dec), "RockAir_Lon": str(lon_dec), "RockAir_Time": time_str}}
                    # calculate delta between Base and remote node
                    RockAir.getGPS()
                    if (RockAir.valid):
                        #print('   Tracker OK: ',RockAir._latitude[3],RockAir._longitude[3])
                        deltaLat = int((RockAir.lat - theData["lat"])*100000)
                        deltaLon = int((RockAir.lon - theData["lon"])*100000)
                    else:
                        print('Tracker ERROR')
                        deltaLat = 0
                        deltaLon = 0
                    # write received data to log file in CSV format in append mode
                    with open(log_filename, 'a') as Log_file:
                        Log_file.write(str(rtc.now()))
                        try:
                            # create message to send
                            theMsg = theData["uid"] + ',' + str(theData["fix"]) + ',' + str(theData["lat"]) + ',' + str(theData["lon"]) + ',' + str(theData["gdt"]) + ',' + str(stats.rssi) + ',' + str(RockAir._latitude[3]) + ',' + str(RockAir._longitude[3])
                            # write data to SD Card
                            Log_file.write(theData["uid"] + ',' + str(theData["fix"]) + ',' + str(theData["lat"]) + ',' + str(theData["lon"]) + ',' + str(theData["bat"]) + ',' + str(stats.rssi) + '\n')
                        except Exception as e:
                            print('   Data Error - No SD card write')
                            print(theData)
                    #with open(log_filename, 'a') as Log_file:
                    #    Log_file.write(theData["uid"] + ',' + str(theData["fix"]) + ',' + str(theData["lat"]) + ',' + str(theData["lon"]) + ',' + str(theData["bat"]) + ',' + str(stats.rssi) + '\n')
                    #Log_file.close()
                else:
                    print ("GPS BAD")
                # print received data to serial port / screen
                print(theData)
            else:
                crcErrorCount += 1
                print ('ERROR - Checksum.')
                print ('  Messages: ' + str(msgCount) + ' recived, ' + str(crcErrorCount) + ' bad' )
                print ('  Recv CRC: ' + str(databytes[:6].decode()))
                print ('  Calc CRC: ' + str(hex(crc16.xmodem(databytes[6:]))))
                print ('  Rcv data: ' + str(databytes.decode()))
    utime.sleep_ms(napTime)
