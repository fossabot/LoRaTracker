import pycom
import machine
from network import LoRa
from network import WLAN
from machine import SD
from microWebSrv import MicroWebSrv
from mqtt import MQTTClient
import socket
import time
import _thread
import struct
import gc
import ujson
import os

# configuration
my_ID = 'BSE1' # unique id of this unit - 4 char string
receiveInterval = 2 # send data every 2 seconds
ledInterval = 1000 # update LED every 1000usec
WLAN_SSID = 'lerdy'
WLAN_PWD = 'lerdy0519'
dataStructure = '4sBffffffl' # structure for packing data into bytes to send
RockAirInterval = 2 * 60 * 1000  # Send data to TracPlus every (2 * 60 * 1000)usec = 2 minutes

GPSFix = False
GPSdatetime = None
lat = None
lon = None
altitude  = None
speed = None
course = None
remote_ID = b''
vBatt = 0.0
msgReceived = False
geoJSON = {}

def LED_thread():
# continuously update the status of the unit via the onboard LED
    global GPSFix
    global msgReceived
    global ledInterval
    ledColour = 0x000000

    while True:
        if GPSFix > 0:
            # GPS OK so set LED to Green
            ledColour = 0x001100
        else:
            # GPS BAD so set LED to RED
            ledColour = 0x110000

        if msgReceived == True:
            # Just Rx a message so set the LED to Purple
            ledColour = 0x110011
            #reset the msg flag as we have actioned it
            msgReceived = False

        pycom.rgbled(ledColour)
        # Hold the set colour for 90% of the ledInterval
        time.sleep_ms(int(ledInterval * 0.9))
        # Blink LED to black for 10% of the ledInterval to indicate code is still running
        pycom.rgbled(0x000000)
        time.sleep_ms(int(ledInterval * 0.1))

def RockAir_thread():
# periodically send data to TracPlus via RockAir via Serial Port
# TESTING FTDI port /dev/cu.usbserial-A800JWP3
    global GPSFix
    global RockAirInterval

    while True:
        if GPSFix > 0:
            # GPS OK so send A message
            #
            print ("GPS OK so send a message")

        else:
            # GPS BAD so don't send message
            #
            print ("GPS BAD so don't send message")

        time.sleep_ms(int(RockAirInterval))

def mqtt_callback(topic, msg):
    # this subroutine would process any message that comes back from MQTT server
    print(msg)

def WWW_routes():
    global geoJSON
    def _geojson(client, response):
        response.WriteResponseJSONOk(geoJSON)
    return [('/gps.json', 'GET', _geojson)]

print ('Starting A-BASE')
print ('   ID: ' + str(my_ID))

print ("Starting Network")
wlan = WLAN(mode=WLAN.STA)
nets = wlan.scan()
for net in nets:
    print('   Found SSID: '+ net.ssid)
    if net.ssid == WLAN_SSID:
        print('   Connecting to: '+ net.ssid)
        wlan.connect(net.ssid, auth=(net.sec, WLAN_PWD), timeout=5000)
        while not wlan.isconnected():
            machine.idle() # save power while waiting
        print("   Connected IP address:" + wlan.ifconfig()[0])
        break

print ("Starting LED")
pycom.heartbeat(False)
pycom.rgbled(0x000011)
_thread.start_new_thread(LED_thread, ())

print ("Starting RockAir")
_thread.start_new_thread(RockAir_thread, ())

print ("Starting UART1")
uart1 = UART(1, 115300, bits=8, parity=None, stop=1)
uart1.init(baudrate=115200, bits=8, parity=None, stop=1)

#print ("Starting SD Card")
#sd = SD()
#os.mount(sd, '/sd')
# start new log file with headers
#with open("/sd/log.csv", 'w') as Log_file:
#    Log_file.write('remote_ID,GPSFix,latitude,longitude,voltage,rssi\n')

print ("Starting Webserver")
routes = WWW_routes()
mws = MicroWebSrv(routeHandlers=routes, webPath="/sd") # TCP port 80 and files in /sd
gc.collect()
mws.Start()         # Starts server in a new thread
gc.collect()

print ("Starting Lora")
lora = LoRa(mode=LoRa.LORA, region=LoRa.AU915)
s = socket.socket(socket.AF_LORA, socket.SOCK_RAW)
s.setblocking(False)

print ('Starting MQTT')
mqtt = MQTTClient(my_ID, "io.adafruit.com",user="agmatthews", password="d9ee3d9d1d5a4f3b860d96beaa9d3413", port=1883)
mqtt.set_callback(mqtt_callback)
mqtt.connect()
mqtt.subscribe(topic="agmatthews/feeds/LORAtest")

print ("Waiting for data")

while True:
    databytes = s.recv(256)
    stats = lora.stats()
    if len(databytes)==36:
        GPSFix = False
        msgReceived = True
        remote_ID, GPSFix, lat, lon, altitude, speed, course, vBatt, GPSdatetime = struct.unpack(dataStructure, databytes)
        if GPSFix:
            # print received data to serial port / screen
            print(remote_ID + ',' + str(GPSFix) + ',' + str(lat) + ',' + str(lon) + ',' + str(altitude) + ',' + str(speed) + ',' + str(course) + ',' + str(vBatt) + ',' + str(GPSdatetime) + ',' + str(stats.rssi))
            # make a geoJSON package of the recived data
            geoJSON = {"geometry": {"type": "Point", "coordinates": [str(lon),str(lat)]}, "type": "Feature", "properties": {"remote_ID": str(remote_ID.decode()), "altitude": str(altitude), "speed": str(speed), "course": str(course), "battery": str(vBatt), "RSSI": str(stats.rssi), "datetime": str(GPSdatetime)}}
            # write received data to log file in CSV format in append mode
            #with open("/sd/log.csv", 'a') as Log_file:
            #    Log_file.write(remote_ID + ',' + str(GPSFix) + ',' + str(lat) + ',' + str(lon) + ',' + str(vBatt) + ',' + str(stats.rssi) + '\n')
            # send data to MQTT server
            #mqtt.publish(topic="agmatthews/feeds/LORAtest", msg=remote_ID + ',' + str(GPSFix) + ',' + str(lat) + ',' + str(lon) + ',' + str(GPSdatetime) + ',' + str(stats.rssi))
            uart1.write(':' + str(vBatt) + ',' + str(gps.timestamp))
        else:
            # print received data to serial port / screen
            print(remote_ID + ",NOGPS," + str(lat) + ',' + str(lon) + ',' + str(altitude) + ',' + str(speed) + ',' + str(course) + ',' + str(vBatt) + ',' + str(GPSdatetime) + ',' + str(stats.rssi))

    time.sleep(receiveInterval)
