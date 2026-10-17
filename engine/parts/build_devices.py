"""Builds engine/parts/devices.json: every device Etch builds from blocks (agent/blocks.py) with no model call.

The table below is the hand-written part: base devices, one per line, as

    id | name | aliases (;-separated) | blocks (space-separated shorthand)

grouped by category. Variants are generated from each base with the modifier words people put in front of or behind
a device ("wireless", "rechargeable", "with a screen", "rgb", ...): each variant changes the block list, so it is a
different board, and gets its own aliases. Run:

    python engine/parts/build_devices.py            # writes devices.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(__file__).with_name("devices.json")

# ---------------------------------------------------------------- base devices
TABLE = """
## trackers and finders
airtag | AirTag-style tag | air tag;airtag;tracker tag;item tracker;bluetooth tracker;tile tracker;smart tag;key finder;tag | wl coin buz btn
gps_tracker | GPS tracker | gps tracker;gps tracking device;location tracker;gps locator;vehicle tracker;car tracker;bike tracker | wl gps lipo usbc btn
gps_logger | GPS logger | gps logger;gps data logger;route logger;trip logger | gps sd lipo usbc btn
pet_tracker | pet tracker | pet tracker;dog tracker;cat tracker;pet gps;collar tracker | wl gps lipo usbc buz
ble_beacon | Bluetooth beacon | beacon;ble beacon;ibeacon;eddystone beacon;bluetooth beacon;proximity beacon | wl coin
wallet_finder | wallet finder | wallet finder;wallet tracker;lost item finder;item finder | wl coin buz
luggage_tracker | luggage tracker | luggage tracker;bag tracker;suitcase tracker | wl gps lipo usbc
kid_locator | child locator | kid tracker;child locator;child tracker | wl gps lipo usbc btn buz
lora_tracker | LoRa tracker | lora tracker;long range tracker;lorawan tracker | lora gps lipo usbc
asset_tag | asset tag | asset tag;inventory tag;asset tracker | wl coin
remote_finder | remote finder | remote finder;tv remote finder | wl coin buz
bike_alarm | bike alarm tracker | bike alarm;bicycle alarm;bike lock alarm | wl dig:vibration buz lipo usbc
drone_tracker | drone tracker | drone tracker;rc plane tracker;model rocket tracker | lora gps lipo buz
hiking_beacon | hiking beacon | hiking beacon;emergency beacon;sos beacon;personal locator beacon | lora gps lipo usbc btn buz
geocache | geocache box | geocache;geocaching box;reverse geocache;puzzle box | gps disp:oled servo btn aa
## audio and music
mp3_player | MP3 player | mp3 player;music player;audio player;walkman;ipod;portable music player | uart:dfplayer spk btn4 lipo usbc disp:oled
mp4_player | MP4 player | mp4 player;video player;media player;portable media player;pmp | disp:tft sd spk btn4 lipo usbc
piano | electronic piano | electronic piano;piano;digital piano;keyboard piano;mini piano;toy piano;keytar | km25 spk pot
synth | synthesizer | synth;synthesizer;synthesiser;mini synth;pocket synth;analog synth | km16 spk pot pot enc
drum_machine | drum machine | drum machine;beat maker;drum pad;sampler;groovebox | km16 spk enc disp:oled
midi_controller | MIDI controller | midi controller;midi keyboard;midi pad;usb midi;launchpad | km16 enc pot pot npx4
theremin | theremin | theremin;air theremin;light theremin | us ldr spk
music_box | music box | music box;melody box;jewelry box | buz btn dig:reed
doorbell_chime | doorbell chime | door chime;chime | spk btn
bluetooth_speaker | Bluetooth speaker | bluetooth speaker;wireless speaker;portable speaker;speaker;boombox | wl spk btn3 lipo usbc rgb
amplifier | amplifier board | amplifier;amp;audio amplifier;speaker amp;class d amp | spk pot
radio_receiver | internet radio | internet radio;wifi radio;radio;fm radio;streaming radio | wl spk enc disp:oled
metronome | metronome | metronome;click track;tempo keeper | buz enc disp:segment leds2
tuner | guitar tuner | guitar tuner;tuner;instrument tuner;ukulele tuner | mic disp:oled
guitar_pedal | guitar effects controller | guitar pedal;effects pedal;footswitch;stomp box;looper pedal | btn2 pot pot leds2 relay
voice_recorder | voice recorder | voice recorder;dictaphone;audio recorder;sound recorder | mic sd btn2 lipo usbc
sound_board | soundboard | soundboard;sound board;meme button board;sound effects box | uart:dfplayer spk btn6
karaoke_mic | karaoke mic | karaoke mic;karaoke microphone;party microphone | mic spk wl lipo usbc
dj_controller | DJ controller | dj controller;dj deck;mixer controller;dj mixer | enc pot pot pot btn6 npx4
xylophone | electronic xylophone | xylophone;glockenspiel;marimba | km12 spk leds3
drum_kit | electronic drum kit | drum kit;e drum;electronic drum;drum trigger | an:force an:force an:force spk
kalimba | electronic kalimba | kalimba;thumb piano;mbira | btn8 spk
ocarina | electronic flute | electronic flute;ocarina;recorder flute;digital wind instrument | btn6 mic spk
noise_maker | white noise machine | white noise machine;noise machine;sleep sound machine;sound machine | spk enc leds2
baby_monitor | baby monitor | baby monitor;nursery monitor;sound monitor | wl mic buz leds2
## keyboards and input
keyboard | keyboard | keyboard;mechanical keyboard;computer keyboard;custom keyboard;split keyboard;60 percent keyboard | kmx48 rgb
macropad | macropad | macropad;macro pad;macro keyboard;stream deck;shortcut pad;hotkey pad | kmx9 enc npx4
numpad | numpad | numpad;number pad;numeric keypad;10 key | kmx17
keypad_lock | keypad lock | keypad lock;code lock;combination lock;pin pad lock | kp servo buz leds2
game_controller | game controller | game controller;gamepad;joypad;controller;game pad | joy btn8 vib wl lipo usbc
arcade_stick | arcade stick | arcade stick;fight stick;arcade controller;arcade joystick | joy btn8
volume_knob | volume knob | volume knob;volume control;media knob;media controller;volume dial | enc rgb
mouse | mouse | mouse;computer mouse;trackball;air mouse | i2c:mpu6050 btn3 wl lipo usbc
presentation_clicker | presentation clicker | presentation clicker;slide clicker;presenter remote;clicker | btn3 wl coin
foot_pedal | foot pedal | foot pedal;foot switch;footswitch | btn2 leds2
button_box | button box | button box;switch panel;sim racing button box;flight panel;cockpit panel | btn8 enc
stenography | steno keyboard | steno keyboard;stenography keyboard;chorded keyboard;chording keyboard | km20
calculator | calculator | calculator;pocket calculator;rpn calculator | kp disp:lcd coin
remote_control | remote control | remote control;tv remote;universal remote;ir remote | irtx btn6 coin
touch_pad | touch pad | touch pad;touchpad;trackpad;touch keypad;capacitive keypad;touch buttons | i2c:mpr121 leds3
gesture_controller | gesture controller | gesture controller;gesture sensor;air gesture;hand gesture remote | i2c:apds9960 wl leds3
dial_pad | rotary dial | rotary dial;dial pad;rotary phone dial | enc disp:oled btn
morse_key | morse code key | morse key;morse code;telegraph key;cw key | btn buz leds2
## wearables
smart_watch | smart watch | smart watch;smartwatch;watch;wrist watch;digital watch | disp:oled i2c:lsm6ds3 vib btn2 wl lipo usbc
fitness_band | fitness band | fitness band;fitness tracker;activity tracker;step counter;pedometer;fitbit | i2c:lsm6ds3 i2c:max30102 vib disp:oled wl lipo usbc
heart_rate | heart rate monitor | heart rate monitor;heart monitor;pulse monitor;pulse oximeter;oximeter;spo2 monitor | i2c:max30102 disp:oled lipo usbc
smart_ring | smart ring | smart ring;ring tracker;nfc ring | i2c:lis3dh wl coin
smart_glasses | smart glasses controller | smart glasses;glasses display;heads up display;hud | disp:oled i2c:bno055 wl lipo usbc btn
led_badge | LED badge | led badge;name badge;conference badge;badge;electronic badge;hackathon badge | npx8 btn2 coin
smart_shoe | smart insole | smart shoe;smart insole;gait tracker;step sensor | an:force an:force i2c:lsm6ds3 wl lipo
posture_sensor | posture sensor | posture sensor;posture corrector;slouch detector | i2c:mpu6050 vib lipo usbc
fall_detector | fall detector | fall detector;fall alarm;elderly fall sensor;fall alert | i2c:mpu6050 buz btn wl lipo usbc
uv_badge | UV exposure badge | uv badge;uv exposure monitor;sun exposure tracker | an:uv leds3 coin
temp_patch | body temperature monitor | body temperature monitor;fever monitor;thermometer patch | i2c:tmp117 wl lipo usbc
haptic_band | haptic wristband | haptic band;vibration wristband;haptic bracelet;buzz bracelet | i2c:drv2605 vib wl lipo usbc btn
led_jacket | LED jacket controller | led jacket;wearable leds;light up clothing;rave outfit;led costume | strip btn lipo usbc mic
cosplay_prop | cosplay prop | cosplay prop;cosplay light;prop lightsaber;lightsaber;magic wand;wand | npx8 i2c:mpu6050 spk btn lipo usbc
helmet_light | bike helmet light | helmet light;bike helmet light;turn signal helmet | npx8 btn2 i2c:lis3dh lipo usbc
## home and smart home
smart_plug | smart plug controller | smart plug;smart switch;wifi switch;smart outlet | wl relay btn leds2
smart_light | smart light | smart light;smart bulb;smart lamp;wifi light | wl npx8 btn
light_switch | smart light switch | smart light switch;wall switch;touch light switch | wl relay dig:touch leds2
thermostat | thermostat | thermostat;smart thermostat;nest;heating controller;hvac controller | i2c:sht31 relay disp:oled enc wl
doorbell | smart doorbell | doorbell;smart doorbell;video doorbell;wireless doorbell | wl btn spk leds2
door_sensor | door sensor | door sensor;window sensor;door alarm;contact sensor;open close sensor | wl dig:reed buz coin
garage_opener | garage door opener | garage door opener;garage opener;gate opener | wl relay dig:reed btn leds2
blinds | smart blinds | smart blinds;blind controller;curtain controller;curtain opener | wl motor btn2 ldr
fan_controller | fan controller | fan controller;smart fan;desk fan;fan speed controller | fan pot i2c:sht31 leds2
humidifier | humidifier controller | humidifier;humidifier controller;mist maker;diffuser;oil diffuser | relay i2c:sht31 btn leds3
air_purifier | air purifier controller | air purifier;air cleaner;hepa filter controller | fan uart:pm25 disp:oled btn
smoke_detector | smoke detector | smoke detector;smoke alarm;fire alarm | an:gas buz btn leds2
co2_monitor | CO2 monitor | co2 monitor;co2 meter;carbon dioxide monitor;air quality monitor;aranet | i2c:scd40 disp:oled buz leds3
leak_detector | water leak detector | leak detector;water leak sensor;flood sensor;water alarm | an:water buz wl coin
mailbox_alert | mailbox alert | mailbox alert;mail notifier;letterbox sensor;mailbox sensor | wl dig:reed aa
presence_sensor | presence sensor | presence sensor;occupancy sensor;room sensor;mmwave sensor | dig:radar wl leds2
smart_lock | smart lock | smart lock;door lock;electronic lock;deadbolt controller | servo kp wl buz
rfid_lock | RFID door lock | rfid lock;rfid door lock;card lock;nfc lock;badge reader | rfid relay buz leds2
fingerprint_lock | fingerprint lock | fingerprint lock;fingerprint scanner;biometric lock;fingerprint reader | uart:fingerprint relay buz leds2
intercom | intercom | intercom;walkie intercom;room intercom | wl mic spk btn
night_light | night light | night light;nightlight;bedside light;sleep light | ldr dig:pir npx4
smart_mirror | smart mirror controller | smart mirror;magic mirror;mirror display | disp:tft dig:pir wl
clock_radio | alarm clock | alarm clock;clock;bedside clock;digital clock;wake up clock | i2c:ds3231 disp:segment buz btn3
sunrise_alarm | sunrise alarm | sunrise alarm;wake up light;sunrise lamp | i2c:ds3231 npx12 disp:oled btn2
ir_blaster | IR blaster | ir blaster;ir hub;smart remote;universal ir;ac remote | wl ir leds2
energy_monitor | energy monitor | energy monitor;power meter;watt meter;current monitor | i2c:ina219 disp:oled wl
outlet_timer | outlet timer | outlet timer;appliance timer;timer switch | relay enc disp:segment
pool_monitor | pool monitor | pool monitor;pool thermometer;hot tub monitor | ntc wl lipo
aquarium | aquarium controller | aquarium controller;fish tank controller;aquarium monitor | ntc relay npx8 wl
smart_doormat | smart doormat | smart doormat;pressure mat;floor mat sensor | an:force buz wl
## environment and weather
weather_station | weather station | weather station;weather monitor;home weather station;mini weather station | i2c:bme280 disp:oled ldr wl
temp_monitor | temperature monitor | temperature monitor;thermometer;temperature sensor;temperature logger | i2c:sht31 disp:oled
humidity_monitor | humidity monitor | humidity monitor;hygrometer;humidity sensor | dht disp:lcd
barometer | barometer | barometer;air pressure monitor;altimeter | i2c:bme280 disp:oled btn
rain_gauge | rain gauge | rain gauge;rain detector;rain sensor | an:water dig:reed wl
uv_meter | UV meter | uv meter;uv index meter;uv sensor | i2c:veml6075 disp:oled
light_meter | light meter | light meter;lux meter;photometer;exposure meter | i2c:bh1750 disp:oled btn
dust_monitor | air dust monitor | dust monitor;pm2.5 monitor;particle monitor;air pollution monitor | i2c:pmsa003i disp:oled leds3
noise_meter | noise meter | noise meter;sound level meter;decibel meter;db meter | mic disp:oled leds3
lightning_detector | storm detector | storm detector;lightning detector;storm alarm | i2c:bme280 buz leds2
anemometer | wind meter | anemometer;wind meter;wind speed sensor | dig:hall disp:oled wl
frost_alarm | frost alarm | frost alarm;freeze alarm;ice warning | ntc buz leds2
radiation_monitor | Geiger counter | geiger counter;radiation monitor;radiation detector | dig:vibration buz disp:oled
water_quality | water quality tester | water quality tester;tds meter;water tester;ph meter | an:water ntc disp:oled
earthquake_alarm | earthquake alarm | earthquake alarm;seismometer;quake detector;vibration monitor | i2c:lis3dh buz leds2
## security
motion_alarm | motion alarm | motion alarm;motion detector;pir alarm;burglar alarm;intruder alarm;security alarm | dig:pir buz leds2 btn
laser_tripwire | laser tripwire | laser tripwire;tripwire;laser alarm;beam break alarm | ldr buz leds2
security_camera | security camera trigger | security camera;camera trigger;trail camera trigger | dig:pir wl leds2
safe_box | electronic safe | safe;electronic safe;lock box;secret box | kp servo buz
vibration_alarm | vibration alarm | vibration alarm;tamper alarm;shake alarm;anti theft alarm | dig:vibration buz coin
panic_button | panic button | panic button;emergency button;sos button;help button | btn wl buz leds2
glass_break | glass break detector | glass break detector;window break alarm | dig:sound buz leds2
sentry | sentry turret | sentry;sentry turret;nerf turret;guard turret | servo2 us buz
access_control | access control panel | access control;keycard reader;door access;entry system | rfid kp relay buz
gas_alarm | gas leak alarm | gas leak alarm;gas detector;gas alarm;propane detector;methane detector | an:gas buz leds2
fire_detector | flame detector | flame detector;fire detector;flame alarm | an:flame buz leds2
## robots and vehicles
robot_car | robot car | robot car;rc car;robot;smart car;obstacle avoiding robot;rover | motor2 us servo aa ir
line_follower | line following robot | line follower;line following robot;line tracer | motor2 dig:line dig:line aa
sumo_robot | sumo robot | sumo robot;mini sumo;battle robot | motor2 us dig:line aa btn
robot_arm | robot arm | robot arm;robotic arm;servo arm;arm controller | servo4 pot pot pot btn
hexapod | hexapod | hexapod;walking robot;spider robot;quadruped;robot dog | servo8 us aa
balance_bot | self balancing robot | balancing robot;self balancing robot;segway robot | i2c:mpu6050 motor2 aa
drone | drone controller | drone;quadcopter;flight controller | motor4 i2c:bno055 wl lipo
rc_boat | RC boat | rc boat;remote boat;boat | motor servo wl aa
rc_plane | RC plane | rc plane;rc airplane;glider controller | servo2 motor wl lipo
pan_tilt | pan tilt mount | pan tilt;camera mount;pan tilt mount;gimbal | servo2 joy
plant_robot | plant rover | plant rover;sun seeking robot;plant robot | motor2 ldr ldr an:soil aa
maze_robot | maze solving robot | maze robot;micromouse;maze solver | motor2 us ir aa
robot_head | robot head | robot head;animatronic head;animatronic eyes;robot eyes | servo4 disp:oled mic
claw_machine | claw machine | claw machine;crane game;claw game | servo3 joy btn buz
conveyor | conveyor belt controller | conveyor;conveyor belt;sorting machine;color sorter | motor servo i2c:tcs34725 btn
pick_place | pick and place controller | pick and place;gantry;xy plotter;plotter | motor2 servo btn2 dig:reed dig:reed
camera_slider | camera slider | camera slider;motion slider;timelapse slider | motor enc disp:oled btn2
## lighting
led_strip_controller | LED strip controller | led strip controller;strip controller;rgb strip;neopixel controller;light strip | strip enc btn wl
mood_lamp | mood lamp | mood lamp;ambient lamp;rgb lamp;color lamp;lava lamp | npx12 dig:touch pot
desk_lamp | desk lamp | desk lamp;reading lamp;study lamp;task light | npx8 dig:touch pot
flashlight | flashlight | flashlight;torch;headlamp;head torch | npx4 btn lipo usbc
bike_light | bike light | bike light;bicycle light;tail light;rear bike light | npx4 btn lipo usbc i2c:lis3dh
traffic_light | traffic light | traffic light;stop light;traffic signal | leds3 btn
christmas_lights | christmas lights | christmas lights;fairy lights;holiday lights;string lights;tree lights | strip btn mic
infinity_mirror | infinity mirror | infinity mirror;mirror tunnel | strip enc
led_cube | LED cube controller | led cube;led matrix cube | disp:matrix btn
led_matrix | LED matrix sign | led matrix;matrix display;scrolling sign;led sign;dot matrix | disp:matrix disp:matrix btn wl
light_painting | light painting stick | light painting stick;pixelstick;light stick | strip sd btn2
stage_light | stage light controller | stage light;dmx light;spotlight;par light | npx8 enc disp:oled
grow_light | grow light | grow light;plant light;indoor grow light | relay i2c:ds3231 ldr leds2
signal_lamp | signal lamp | signal lamp;status light;andon light;tower light;stack light | leds3 buz wl
vu_meter | VU meter | vu meter;audio visualizer;music visualizer;sound reactive lights | mic npx12
candle | electronic candle | electronic candle;led candle;flameless candle | leds2 dig:touch coin
nightlight_toy | star projector | star projector;night sky projector;galaxy light | npx8 motor btn
## toys and games
reaction_game | reaction game | reaction game;reaction timer;reflex game;whack a mole;speed game | btn4 leds4 buz disp:segment
simon | Simon game | simon;simon says;memory game;color memory game | btn4 leds4 buz
dice | electronic dice | dice;electronic dice;led dice;digital dice | leds7 btn i2c:lis3dh coin
handheld_console | handheld game console | handheld console;game console;game boy;gameboy;handheld game;retro console;tamagotchi;virtual pet | disp:tft btn6 spk lipo usbc
pong | pong game | pong;pong game;paddle game | disp:oled pot pot buz
snake | snake game | snake game;snake;tetris;tetris game;brick game | disp:matrix joy buz
quiz_buzzer | quiz buzzer | quiz buzzer;game show buzzer;trivia buzzer;buzzer system;jeopardy buzzer | btn4 leds4 buz
laser_tag | laser tag gun | laser tag;laser tag gun;ir tag;infrared tag | ir btn spk leds3 lipo
talking_toy | talking toy | talking toy;talking doll;speaking toy;furby | uart:dfplayer spk btn dig:tilt
magic_8ball | magic 8 ball | magic 8 ball;fortune teller;decision maker | disp:oled dig:tilt coin
spinning_top | LED spinning top | spinning top;led top;fidget spinner light | npx4 dig:tilt coin
pinball | pinball controller | pinball;pinball machine;flipper game | btn4 servo2 buz disp:segment
toy_robot | toy robot | toy robot;robot toy;bot toy | servo2 us spk leds2 aa
rubber_duck | debugging duck | rubber duck;rubber duck debugger;desk duck | spk btn npx4
slot_machine | slot machine | slot machine;fruit machine;one armed bandit | disp:oled btn buz npx4
tic_tac_toe | tic tac toe | tic tac toe;noughts and crosses | km9 npx9 buz
puzzle_lock | escape room puzzle | escape room puzzle;escape room prop;puzzle lock;riddle box | kp servo leds3 buz
board_game_timer | game timer | game timer;chess clock;turn timer;board game timer | btn2 disp:segment buz
fidget | fidget toy | fidget;fidget toy;fidget box;fidget board;fidget cube;fidget clicker;stress toy;busy board;clicky toy;stim toy | btn4 enc vib npx4
rc_car_remote | RC transmitter | rc transmitter;radio controller;rc remote;transmitter | joy nrf btn4 aa
## health and fitness
pill_reminder | pill reminder | pill reminder;medication reminder;pill box;pill dispenser | i2c:ds3231 buz leds4 btn4
posture_trainer | standing desk reminder | stand reminder;sit stand reminder;break reminder | us buz leds2
hydration | water bottle tracker | water bottle tracker;hydration reminder;smart bottle;drink reminder | hx buz leds3 lipo
scale | digital scale | scale;digital scale;kitchen scale;weighing scale;bathroom scale;luggage scale | hx disp:segment btn2
thermometer_ir | IR thermometer | ir thermometer;forehead thermometer;non contact thermometer;infrared thermometer | i2c:mlx90614 disp:oled btn buz
breath_trainer | breathing trainer | breathing trainer;meditation light;breathing light;calm light | npx8 btn
stopwatch | stopwatch | stopwatch;lap timer;sports timer | btn2 disp:segment buz
jump_rope | smart jump rope | jump rope counter;skipping counter;rep counter | dig:hall disp:oled coin
workout_timer | workout timer | workout timer;interval timer;hiit timer;tabata timer;boxing timer | btn3 disp:segment buz leds2
emg_monitor | muscle sensor display | emg monitor;muscle sensor;muscle activity monitor;myoware | an:emg disp:oled leds3
reaction_trainer | reaction trainer | reaction trainer;agility lights;blazepod;sports reaction lights | npx4 dig:touch buz wl lipo
sleep_tracker | sleep tracker | sleep tracker;sleep monitor;snore detector | i2c:lis3dh mic wl lipo usbc
grip_trainer | grip strength meter | grip strength meter;grip trainer;dynamometer | hx disp:oled
massager | massager controller | massager;massage controller;vibration massager | vib vib pot btn lipo usbc
## garden and agriculture
plant_monitor | plant monitor | plant monitor;plant sensor;soil sensor;soil moisture monitor;plant care | an:soil ldr disp:oled wl
auto_waterer | automatic plant waterer | plant waterer;automatic watering;self watering;plant watering;irrigation controller;sprinkler controller | an:soil pump btn leds2
greenhouse | greenhouse controller | greenhouse controller;greenhouse monitor;grow tent controller | i2c:sht31 an:soil relay fan disp:oled wl
hydroponics | hydroponics controller | hydroponics controller;hydroponic system;aquaponics controller | pump ntc an:water relay wl
compost | compost monitor | compost monitor;compost thermometer | ntc an:soil wl lipo
beehive | beehive monitor | beehive monitor;hive scale;bee monitor | hx i2c:sht31 mic lora lipo
chicken_door | chicken coop door | chicken coop door;coop door opener;automatic coop door | motor ldr dig:reed dig:reed btn
bird_feeder | smart bird feeder | bird feeder;smart bird feeder;bird counter | dig:pir hx wl
pest_repeller | pest repeller | pest repeller;mosquito repeller;rodent repeller;bird scarer | dig:pir buz leds2
weather_vane | soil station | farm sensor;field station;agriculture sensor | an:soil i2c:bme280 lora lipo
frost_protector | frost protector | frost protector;frost cloth controller | ntc relay buz
## kitchen
kitchen_timer | kitchen timer | kitchen timer;egg timer;cooking timer;timer;countdown timer | enc disp:segment buz
coffee_scale | coffee scale | coffee scale;espresso scale;pour over scale | hx disp:oled btn2 lipo usbc
sous_vide | sous vide controller | sous vide;sous vide controller;water bath controller | ntc relay disp:oled enc
smart_kettle | kettle controller | kettle controller;tea kettle controller;tea timer | ntc relay btn buz
fridge_alarm | fridge alarm | fridge alarm;fridge monitor;freezer alarm;fridge door alarm | i2c:sht31 dig:reed buz wl
meat_thermometer | meat thermometer | meat thermometer;bbq thermometer;grill thermometer;oven thermometer | ntc ntc disp:oled buz wl
bread_proofer | bread proofer | bread proofer;proofing box;dough proofer;yogurt maker;fermentation controller | ntc relay disp:oled btn2
drink_mixer | drink mixer | drink mixer;cocktail machine;bartender robot;drink dispenser | pump pump pump btn3 disp:oled
pet_feeder | pet feeder | pet feeder;automatic pet feeder;cat feeder;dog feeder;fish feeder | servo i2c:ds3231 btn disp:oled
## office and desk
desk_clock | desk clock | desk clock;word clock;binary clock;nixie style clock | i2c:ds3231 npx12 btn2
pomodoro | pomodoro timer | pomodoro;pomodoro timer;focus timer;study timer | enc disp:oled buz npx4
busy_light | busy light | busy light;on air light;meeting light;do not disturb light | wl npx8 btn
notification_light | notification light | notification light;email notifier;notification lamp | wl npx4 btn
desk_display | desk dashboard | desk display;dashboard;info display;stock ticker;crypto ticker;weather display | wl disp:tft btn2
eink_display | e-ink display | e-ink display;eink display;e paper display;epaper display;e ink calendar;e ink frame | wl disp:epaper btn lipo usbc
usb_hub_switch | USB power switch | usb power switch;usb switch;power switcher | relay btn2 leds2
cable_tester | cable tester | cable tester;continuity tester;network cable tester | leds8 btn buz
label_counter | tally counter | tally counter;clicker counter;people counter;counter | btn2 disp:segment
visitor_counter | visitor counter | visitor counter;footfall counter;door counter;entrance counter | dig:ir_obstacle dig:ir_obstacle disp:segment
standing_desk | standing desk controller | standing desk controller;desk height controller | motor btn3 us disp:segment
## tools and measurement
multimeter | voltmeter | voltmeter;multimeter;volt meter;battery tester | an:current disp:oled btn
distance_meter | distance meter | distance meter;range finder;ultrasonic ruler;tape measure;laser distance meter | i2c:vl53l0x disp:oled btn
parking_sensor | parking sensor | parking sensor;parking assistant;garage parking sensor;backup sensor | us npx8 buz
level | digital level | digital level;spirit level;inclinometer;angle gauge;bubble level | i2c:mpu6050 disp:oled btn
compass | digital compass | digital compass;compass;electronic compass | i2c:qmc5883 disp:oled coin
tachometer | tachometer | tachometer;rpm meter;speed sensor;rpm counter | dig:hall disp:segment
oscilloscope | mini oscilloscope | oscilloscope;mini scope;scope;signal viewer | pot disp:tft btn2
logic_probe | logic probe | logic probe;logic tester;signal probe | leds3 buz
signal_generator | signal generator | signal generator;function generator;tone generator | enc disp:oled spk
color_sensor | color picker | color sensor;color picker;colour detector;color detector | i2c:tcs34725 rgb disp:oled btn
spectrometer | spectrometer | spectrometer;spectral sensor;light analyzer | i2c:as7341 disp:oled btn
data_logger | data logger | data logger;datalogger;sensor logger;logger | i2c:bme280 sd i2c:ds3231 btn lipo usbc
load_tester | force gauge | force gauge;force meter;load tester;tension meter | hx disp:oled btn
stud_finder | metal detector | metal detector;stud finder;magnet finder | dig:hall buz leds3
power_monitor | power monitor | power monitor;usb power meter;usb tester;current meter | i2c:ina219 disp:oled
soldering_timer | fume extractor | fume extractor;solder fume fan;smoke absorber | fan btn leds2
heat_gun | heater controller | heater controller;heat bed controller;hot plate controller;reflow controller | ntc relay disp:oled enc
counter_geiger | event counter | event counter;pulse counter;frequency counter | dig:ir_obstacle disp:segment btn
## communication and radio
walkie_talkie | walkie talkie | walkie talkie;two way radio;handheld radio | lora mic spk btn lipo usbc
pager | pager | pager;beeper;text pager;messenger | lora disp:oled buz btn2 lipo usbc
mesh_node | mesh node | mesh node;meshtastic;lora node;off grid messenger | lora disp:oled btn lipo usbc
gsm_alarm | SMS alarm | sms alarm;gsm alarm;text alert;sms notifier;cellular alarm | uart:sim800 dig:pir buz
ham_keyer | ham radio keyer | keyer;cw keyer;ham keyer | btn2 enc buz
nrf_remote | 2.4 GHz remote | 2.4ghz remote;nrf remote;wireless remote;rf remote | nrf btn4 coin
rf_receiver | wireless receiver | wireless receiver;rf receiver;radio receiver module | nrf relay leds2
wifi_button | Wi-Fi button | wifi button;dash button;iot button;smart button | wl btn rgb coin
bluetooth_bridge | Bluetooth serial bridge | bluetooth serial;bluetooth module board;serial bridge | uart:hc05 leds2 btn
satellite_tracker | satellite tracker display | iss tracker;satellite tracker;space station tracker | wl disp:oled servo2
nfc_reader | NFC reader | nfc reader;nfc tag reader;nfc writer;rfid reader | i2c:pn532 buz leds2
morse_beacon | Morse beacon | morse beacon;cw beacon | leds2 buz lora
## education and kits
blink_kit | blink board | blink;blinky;led blinker;blinking led;first board | leds2 btn
learning_kit | sensor learning kit | learning kit;starter kit;sensor kit;experiment board;stem kit;education board | ldr ntc pot btn2 leds3 buz disp:oled
logic_trainer | logic trainer | logic trainer;digital logic trainer;binary trainer;binary counter | btn4 leds4
morse_trainer | morse trainer | morse trainer;morse code trainer;morse code practice | btn buz disp:oled
quiz_board | quiz board | quiz board;answer board;classroom responder | btn4 leds4 wl
periodic_table | science demo board | science demo;physics demo;lab demo | i2c:mpu6050 ldr ntc disp:oled
coding_robot | coding robot | coding robot;educational robot;turtle robot;logo turtle | motor2 servo dig:line buz aa
## display and clock
digital_clock | digital clock | wall clock;led clock;seven segment clock | i2c:ds3231 disp:segment btn2
word_clock | word clock | word clock;text clock;qlocktwo | i2c:ds3231 strip ldr
countdown | countdown display | countdown;event countdown;countdown clock;new year countdown | wl disp:segment buz
scoreboard | scoreboard | scoreboard;score keeper;score display;ping pong scoreboard | btn4 disp:segment disp:segment
lcd_display | text display | lcd display;text display;message board;message display | disp:lcd btn2 wl
oled_display | OLED display board | oled display;oled screen;small screen;tiny display | disp:oled btn2
tft_display | color display | color display;tft screen;lcd screen;graphics display | disp:tft btn2
photo_frame | digital photo frame | photo frame;digital photo frame;picture frame | disp:tft sd btn2 dig:pir
name_tag | e-paper name tag | e paper badge;eink badge;name tag;e paper name tag | disp:epaper btn coin
ticker | scrolling ticker | ticker;scrolling text;news ticker | disp:matrix disp:matrix wl
binary_watch | binary watch | binary watch;led watch;binary clock watch | leds6 btn i2c:ds3231 coin
## pets and animals
pet_door | pet door | pet door;cat flap;dog door;smart pet door | rfid servo leds2
cat_toy | cat laser toy | cat toy;laser toy;cat laser;pet toy | servo2 irtx btn
pet_water | pet water fountain | pet fountain;water fountain;cat fountain | pump an:water leds2
pet_cam | pet monitor | pet monitor;pet camera;pet treat dispenser;treat dispenser | wl servo mic spk
litter_box | litter box monitor | litter box monitor;litter sensor | dig:pir hx wl
bark_detector | bark detector | bark detector;bark counter;bark alarm | mic leds3 wl
horse_feeder | livestock monitor | livestock monitor;animal monitor;barn sensor | i2c:sht31 lora lipo
## automotive
car_obd | car dashboard | car dashboard;car display;dash display;car gauge | disp:oled i2c:mpu6050 wl
tire_alarm | tire pressure alarm | tire pressure monitor;tpms;tire alarm | an:force buz wl
car_alarm | car alarm | car alarm;vehicle alarm;auto alarm | dig:vibration dig:pir buz relay
dashcam_trigger | crash detector | crash detector;impact sensor;accident detector;dash cam trigger | i2c:lis3dh buz wl lipo
headlight | headlight controller | headlight controller;auto headlight;light sensor switch | ldr relay leds2
turn_signal | turn signal | turn signal;indicator light;blinker | btn2 npx8
battery_monitor | battery monitor | battery monitor;car battery monitor;voltage monitor | an:current disp:oled buz
speedometer | bike speedometer | speedometer;bike computer;cycle computer;odometer;trip computer | dig:hall disp:oled btn coin
fuel_gauge | tank level monitor | tank level;water tank level;fuel gauge;level sensor;tank monitor | us disp:segment wl
## accessibility and assistive
braille | braille display | braille display;refreshable braille;braille cell | servo6 btn2
smart_cane | smart cane | smart cane;obstacle detector cane;blind cane;walking cane | us vib buz lipo usbc
hearing_alert | sound alert | sound alert;hearing aid alert;deaf alert;doorbell flasher | mic npx4 vib
switch_adapter | assistive switch | assistive switch;accessibility switch;big button;adaptive switch | btn2 relay leds2
sip_puff | sip and puff controller | sip and puff;sip puff;mouth controller | an:force wl leds2
## storage and logging
sd_logger | SD card logger | sd card logger;sd logger;sd card board | sd i2c:ds3231 btn leds2
temp_logger | temperature logger | temp logger;cold chain logger;vaccine fridge logger | i2c:sht31 sd i2c:ds3231 lipo usbc
flight_recorder | rocket flight computer | flight computer;rocket altimeter;model rocket computer;flight recorder | i2c:bme280 i2c:mpu6050 sd buz lipo
black_box | trip recorder | black box;trip recorder;event recorder | i2c:lis3dh gps sd lipo usbc
## energy and power
solar_tracker | solar tracker | solar tracker;sun tracker;solar panel tracker | servo2 ldr ldr ldr
power_bank | power bank | power bank;battery pack;portable charger;usb charger | lipo usbc leds4 btn
battery_charger | battery charger | battery charger;lipo charger;charger board;charging board | lipo usbc leds2
ups | mini UPS | mini ups;ups;backup power;uninterruptible power supply | lipo usbc relay leds2
solar_monitor | solar monitor | solar monitor;solar power meter;solar charge monitor | i2c:ina219 disp:oled wl
dummy_load | electronic load | dummy load;electronic load;load bank | motor pot disp:oled
## holiday and decor
halloween_prop | Halloween prop | halloween prop;jump scare;spooky prop;haunted prop;scary skeleton | dig:pir spk servo npx4
pumpkin | light-up pumpkin | pumpkin;jack o lantern;halloween pumpkin | npx4 dig:pir buz
xmas_tree | Christmas tree ornament | christmas tree;xmas tree;christmas ornament;tree ornament | npx8 btn coin
advent | advent calendar | advent calendar;countdown calendar | i2c:ds3231 npx12 btn
snow_globe | snow globe | snow globe;music globe | npx4 motor buz btn
greeting_card | musical greeting card | greeting card;birthday card;musical card;pop up card | buz dig:ir_obstacle coin
party_lights | party lights | party lights;disco light;party light;strobe light | strip mic btn
menorah | electronic menorah | menorah;candle holder;kinara | leds9 btn
fireworks | fireworks igniter | fireworks controller;ignition controller;launch controller | relay btn2 buz leds2
## misc
fan_cooler | PC fan controller | pc fan controller;computer fan controller;case fan controller | fan fan ntc pot
hand_sanitizer | touchless dispenser | soap dispenser;touchless dispenser;hand sanitizer dispenser;automatic dispenser | us pump leds2
trash_can | smart trash can | smart trash can;automatic trash can;smart bin;touchless bin | us servo leds2
umbrella | umbrella reminder | umbrella reminder;rain reminder | wl dig:pir leds2 buz
lost_and_found | proximity alarm | proximity alarm;anti lost alarm;phone leash;proximity tag | wl buz coin btn
coin_counter | coin counter | coin counter;coin sorter;piggy bank;money box | dig:ir_obstacle disp:segment buz
vending | vending machine controller | vending machine;candy dispenser;gumball machine;snack dispenser | servo2 kp disp:lcd buz
dispenser | pill dispenser | dispenser;auto dispenser;cereal dispenser | servo btn leds2
voting | voting machine | voting machine;poll box;feedback box;rating buttons | btn4 leds4 disp:segment
sign_language | sign language glove | sign language glove;data glove;flex glove;gesture glove | an:flex an:flex an:flex i2c:mpu6050 wl
smart_pen | smart pen | smart pen;digital pen;stylus | i2c:mpu6050 btn wl lipo
smart_cup | smart cup | smart cup;smart mug;coaster | ntc npx4 hx
sleep_mask | light sleep mask | light alarm mask;sleep mask | npx4 vib i2c:ds3231 lipo
"""

# shorthand -> block spec
SIMPLE = {
    "wl": ["wireless", {}], "gps": ["gps", {}], "lipo": ["lipo", {}], "usbc": ["usb_c", {}],
    "coin": ["coin_cell", {}], "aa": ["battery_pack", {}], "buz": ["buzzer", {}], "spk": ["speaker", {}],
    "sd": ["sd_card", {}], "enc": ["encoder", {}], "joy": ["joystick", {}], "ldr": ["ldr", {}], "ntc": ["ntc", {}],
    "dht": ["dht", {}], "mic": ["mic", {}], "rgb": ["rgb_led", {}], "ir": ["ir", {}],
    "irtx": ["ir", {"rx": False}], "irrx": ["ir", {"tx": False}], "rfid": ["rfid", {}], "lora": ["lora", {}],
    "nrf": ["nrf24", {}], "kp": ["keypad", {}], "hx": ["load_cell", {}], "relay": ["relay", {}],
    "us": ["ultrasonic", {}], "strip": ["led_strip", {}], "pot": ["pot", {}], "btn": ["button", {}],
    "servo": ["servo", {}], "motor": ["motor", {}],
    "vib": ["motor", {"label": "vibration motor"}], "fan": ["motor", {"label": "fan"}],
    "pump": ["motor", {"label": "pump"}],
}  # fmt: skip


def block(tok: str) -> list:
    if tok in SIMPLE:
        return json.loads(json.dumps(SIMPLE[tok]))
    if ":" in tok:
        kind, arg = tok.split(":", 1)
        return {
            "i2c": ["i2c_sensor", {"chip": arg}],
            "disp": ["display", {"kind": arg}],
            "an": ["analog_sensor", {"kind": arg}],
            "dig": ["digital_sensor", {"kind": arg}],
            "uart": ["uart_module", {"kind": arg}],
        }[kind]
    m = re.fullmatch(r"([a-z]+?)(\d+)", tok)
    if m:
        name, n = m.group(1), int(m.group(2))
        if name == "kmx":
            return ["key_matrix", {"n": n, "switch": "mx"}]
        if name == "km":
            return ["key_matrix", {"n": n}]
        if name == "npx":
            return ["neopixel", {"n": n}]
        if name == "leds":
            return ["leds", {"n": n}]
        if name in ("btn", "servo", "motor"):
            return [{"btn": "button", "servo": "servo", "motor": "motor"}[name], {"n": n}]
    raise ValueError(f"unknown block shorthand {tok!r}")


# modifier variants: (id suffix, name pattern, alias patterns, blocks added, blocks it cannot sit next to)
VARIANTS = [
    ("wireless", "wireless {}", ["wireless {}", "bluetooth {}", "wifi {}", "smart {}", "connected {}", "iot {}"],
     ["wl"], {"wl"}),
    ("rechargeable", "rechargeable {}", ["rechargeable {}", "battery powered {}", "portable {}", "cordless {}"],
     ["lipo", "usbc"], {"lipo", "coin", "aa"}),
    ("display", "{} with a display", ["{} with display", "{} with screen", "{} with oled", "{} with a screen"],
     ["disp:oled"], {"disp:oled", "disp:tft", "disp:epaper", "disp:lcd"}),
    ("buzzer", "{} with an alarm", ["{} with buzzer", "{} with alarm", "{} with beeper", "beeping {}"],
     ["buz"], {"buz", "spk"}),
    ("rgb", "RGB {}", ["rgb {}", "colour {}", "color {}", "light up {}", "led {}"], ["rgb"], {"rgb"}),
    ("knob", "{} with a knob", ["{} with knob", "{} with encoder", "{} with dial"], ["enc"], {"enc"}),
    ("speaker", "{} with a speaker", ["{} with speaker", "talking {}", "{} with sound"], ["spk"], {"spk", "buz"}),
    ("usbc", "USB-C {}", ["usb c {}", "usb-c {}", "usb powered {}"], ["usbc"], {"usbc"}),
    ("logging", "logging {}", ["logging {}", "{} with sd card", "{} logger", "recording {}"], ["sd"], {"sd"}),
    ("gps", "{} with GPS", ["{} with gps", "gps {}"], ["gps"], {"gps"}),
]  # fmt: skip

PAIRS = [("wireless", "rechargeable"), ("wireless", "display"), ("rechargeable", "display"), ("wireless", "buzzer"),
         ("rechargeable", "rgb"), ("display", "knob"), ("wireless", "rgb")]  # fmt: skip


def parse() -> list[dict]:
    out, cat = [], "misc"
    for line in TABLE.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("##"):
            cat = line[2:].strip()
            continue
        did, name, aliases, blocks = (x.strip() for x in line.split("|"))
        out.append(
            {
                "id": did,
                "name": name,
                "category": cat,
                "aliases": [a.strip() for a in aliases.split(";") if a.strip()],
                "short": blocks.split(),
            }
        )
    return out


def build() -> dict:
    bases = parse()
    devices = []
    seen_alias: set[str] = set()

    def emit(did, name, cat, aliases, short, base, variant):
        al = []
        for a in aliases:
            a = re.sub(r"\s+", " ", a.lower()).strip()
            if a and a not in seen_alias:
                seen_alias.add(a)
                al.append(a)
        devices.append({
            "id": did, "name": name, "category": cat, "base": base, "variant": variant,
            "aliases": al, "blocks": [block(t) for t in short],
            "wireless": "wl" in short,
        })  # fmt: skip

    for b in bases:
        emit(b["id"], b["name"], b["category"], [b["name"]] + b["aliases"], b["short"], b["id"], None)
    vmap = {v[0]: v for v in VARIANTS}
    for b in bases:
        primary = b["aliases"][:3]
        for key, npat, apats, add, clash in VARIANTS:
            if clash & set(b["short"]) or (key == "gps" and "lora" in b["short"]):
                continue
            first = b["name"].split()[0]
            low = first[1:].islower() if len(first) > 1 else True
            nm = npat.format(b["name"][0].lower() + b["name"][1:] if low else b["name"])
            emit(f"{b['id']}+{key}", nm[0].upper() + nm[1:], b["category"],
                 [p.format(a) for a in primary for p in apats], b["short"] + add, b["id"], key)  # fmt: skip
        for k1, k2 in PAIRS:
            v1, v2 = vmap[k1], vmap[k2]
            if (v1[4] | v2[4]) & set(b["short"]):
                continue
            nm = f"{v1[1].format(b['name'])}".replace("{}", "")
            words = {"wireless": "wireless", "rechargeable": "rechargeable", "rgb": "rgb"}
            pre = " ".join(words[k] for k in (k1, k2) if k in words)
            post = " and ".join({"display": "a display", "buzzer": "an alarm", "knob": "a knob"}[k]
                                for k in (k1, k2) if k not in words)  # fmt: skip
            nm = (pre + " " + b["name"]).strip() + (f" with {post}" if post else "")
            al = [((pre + " " + a).strip() + (f" with {post.replace('a ', '').replace('an ', '')}" if post else ""))
                  for a in primary]  # fmt: skip
            emit(f"{b['id']}+{k1}+{k2}", nm[0].upper() + nm[1:], b["category"], al,
                 b["short"] + v1[3] + v2[3], b["id"], f"{k1}+{k2}")  # fmt: skip
    cats: dict[str, int] = {}
    for d in devices:
        cats[d["category"]] = cats.get(d["category"], 0) + 1
    return {
        "about": "Device types Etch builds from code-wired blocks (agent/blocks.py). Generated by build_devices.py; "
        "base devices are hand-written, variants add the blocks their modifier words name.",
        "count": len(devices),
        "bases": len(bases),
        "categories": cats,
        "devices": devices,
    }


if __name__ == "__main__":
    db = build()
    OUT.write_text(json.dumps(db, indent=1) + "\n")
    print(f"{db['count']} devices ({db['bases']} base) in {len(db['categories'])} categories -> {OUT}")
