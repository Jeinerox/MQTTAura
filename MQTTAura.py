import time
from paho.mqtt import client as mqtt_client
import win32com.client
import threading
from multiprocessing import Process, Array, Value
import math
import signal
import sys
import datetime
import logging
import subprocess

import ctypes

broker = 'your_broker'
port = 5392
ONOFFSTATE_ILLUMINATION        = "/pc/set/onoffstate_illumination"
ONOFFSTATE_ILLUMINATION_STATUS = "/pc/status/onoffstate_illumination"      
BRIGHTNESS_ILLUMINATION        = "/pc/set/brightness_illumination"
BRIGHTNESS_ILLUMINATION_STATUS = "/pc/status/brightness_illumination"
RGB_ILLUMINATION               = "/pc/set/RGBstate_illumination"
RGB_ILLUMINATION_STATUS        = "/pc/status/RGBstate_illumination"
ALL                            = "/pc/set/#"
LWT                            = "/pc/status/LWT"        

    
client_id = 'pc'
username = 'your_username'
password = 'your_password'

Rmult = 1
Gmult = 0.7
Bmult = 0.8



class Color:
    def __init__(self):
        self.current = 255
        self.mqtt = 255 
    def isequal(self):
        if self.current == self.mqtt:
            return True
        return False


R = 0
G = 0
B = 0
RGBString = "0,0,0"
onoffstate = 0
brightness = 100
target_colors = Array('i', [0, 0, 0]) 
shouldrun = Value('i', 1)
shouldexit = Value('i', 0)
auraProcess = None
client = None


def convert_to_hex(color):
    return (color[2] << 16) | (color[1] << 8) | color[0]
 
 
def way(a,b, value):
    if abs(a-b)<21 and value > 1:
        value = 1
    return -value if a > b else value if a < b else 0
        

def hardwareApply(R,G,B, devices):
    HEXColor = convert_to_hex((R, G, B))    
    for dev in devices:    
        for i in range(dev.Lights.Count):    # Use index
            dev.Lights(i).color = HEXColor
        dev.Apply()


def auraProcessFunc(target_colors, shouldrun, shouldexit):
    try:
        time.sleep(5)
        auraSdk = win32com.client.Dispatch("aura.sdk.1")
        auraSdk.SwitchMode()
        devices = auraSdk.Enumerate(0x00010000)     
        
        Rcurrent = 0
        Gcurrent = 0
        Bcurrent = 0
    except KeyboardInterrupt:
        return
    try:
        while shouldexit.value == 0:
            if shouldrun.value == 0:
                time.sleep(0.2)
                continue
            
            if Rcurrent == target_colors[0] and Gcurrent == target_colors[1] and Bcurrent == target_colors[2]:
                shouldrun.value = 0
                continue
                
            Rcurrent += way(Rcurrent, target_colors[0], 1)
            Gcurrent += way(Gcurrent, target_colors[1], 1)
            Bcurrent += way(Bcurrent, target_colors[2], 1)            
            hardwareApply(Rcurrent, Gcurrent, Bcurrent, devices)
    except KeyboardInterrupt:
        target_colors[0], target_colors[1], target_colors[2] = 0, 0, 0
        while not (Rcurrent == target_colors[0] and Gcurrent == target_colors[1] and Bcurrent == target_colors[2]):
            Rcurrent += way(Rcurrent, target_colors[0], 5)
            Gcurrent += way(Gcurrent, target_colors[1], 5)
            Bcurrent += way(Bcurrent, target_colors[2], 5)             
            hardwareApply(Rcurrent, Gcurrent, Bcurrent, devices)    

def colorCorrection(color, n, scale):
    return int (( (color/255) **(1/n) ) * scale * 255)


def apply():
    global target_colors, lock_set
    brightMultiplier = brightness/100  
    Rout, Gout, Bout = 0,0,0
    if not onoffstate:
        Rout, Gout, Bout = 0,0,0
    else:
        Rout = colorCorrection(R*brightMultiplier, 1.2, Rmult)  
        Gout = colorCorrection(G*brightMultiplier, 1, Gmult)  
        Bout = colorCorrection(B*brightMultiplier, 1, Bmult)  
    target_colors[0] = Rout
    target_colors[1] = Gout
    target_colors[2] = Bout
    shouldrun.value = 1
    
def send(client):     
    client.publish(ONOFFSTATE_ILLUMINATION_STATUS,  onoffstate,            1)
    client.publish(BRIGHTNESS_ILLUMINATION_STATUS,  brightness,            1)
    client.publish(RGB_ILLUMINATION_STATUS,         RGBString,             1)

def connect_mqtt() -> mqtt_client:
    def on_connect(client, userdata, flags, rc):
        pass
        if rc == 0:
            print("Connected to MQTT Broker!")
            subscribe(client) 
            client.publish(LWT, payload=1, qos=0, retain=True)
        else:
            print("Failed to connect, return code %dn", rc)

    client = mqtt_client.Client(client_id)
    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.will_set(LWT, payload=0, qos=0, retain=True)
    client.connect(broker, port)  
    return client


def on_disconnect(client, userdata, rc):
    print("Unexpected MQTT disconnection. Will auto-reconnect")
  
    
def parse_color_string(color):
    return [int(x) for x in color.split(",")]


def subscribe(client: mqtt_client):
    def on_message(client, userdata, msg):
        global R,G,B,onoffstate,brightness,RGBString,lock_set,idiotMutex  
        print(f"Received `{msg.payload.decode()}` from `{msg.topic}` topic")
        if msg.topic == RGB_ILLUMINATION:
            RGBString = msg.payload.decode()
            R,G,B = parse_color_string(RGBString)
            if RGBString == "255,10,10":
                G, B = 0, 0
            #onoffstate = 1
        elif msg.topic == BRIGHTNESS_ILLUMINATION:
            brightness = int(msg.payload.decode())
            #onoffstate = 1
        elif msg.topic == ONOFFSTATE_ILLUMINATION:
            onoffstate = int(msg.payload.decode())
        
        apply()    
        send(client)
  

        
    client.subscribe(ALL)
    client.on_message = on_message


def signal_handler(sig, frame):
    global onoffstate
    onoffstate = 0
    send(client)    
    auraProcess.join()
    sys.exit(0)
    
    
def monitor_sleep():
    last_check_time = time.time()
    while True:
        time.sleep(1)        
        if time.time() - last_check_time > 5:
            restart_process()
        last_check_time = time.time()  


def restart_process():
    global target_colors, shouldrun, auraProcess, shouldexit, client
    if auraProcess.is_alive():
        auraProcess.terminate()
    auraProcess = Process(target=auraProcessFunc, args=(target_colors, shouldrun, shouldexit), daemon=True)
    auraProcess.start()
    

def main():
    time.sleep(5)
    global target_colors, shouldrun, auraProcess, shouldexit, client
    signal.signal(signal.SIGINT, signal_handler)
    auraProcess = Process(target=auraProcessFunc, args=(target_colors, shouldrun, shouldexit), daemon=True)
    auraProcess.start()
    client = connect_mqtt()
    client.on_disconnect = on_disconnect    
    sleep_monitor_thread = threading.Thread(target=monitor_sleep, daemon=True)
    sleep_monitor_thread.start()   
    client.loop_forever()    
    
    

if __name__ == '__main__':
    main()    
