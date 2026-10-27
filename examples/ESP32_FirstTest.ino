// ESP32 first test: serial messages and the GPIO2 LED.
const int LED_PIN = 2;
unsigned long counter = 0;

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  delay(500);
  Serial.println("ESP32 started");
}

void loop() {
  Serial.print("ESP32 is running. Count: ");
  Serial.println(counter++);

  digitalWrite(LED_PIN, HIGH);
  delay(500);
  digitalWrite(LED_PIN, LOW);
  delay(500);
}
