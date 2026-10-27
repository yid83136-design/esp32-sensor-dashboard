from machine import Pin
import time

# GPIO2 usually connects to the small LED on this board.
led = Pin(2, Pin.OUT)
count = 0

while True:
    print("MicroPython is running. Count:", count)
    count += 1
    led.value(1)
    time.sleep(0.5)
    led.value(0)
    time.sleep(0.5)
