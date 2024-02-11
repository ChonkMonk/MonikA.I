import os
import re
import subprocess
import torch
import yaml
import numpy as np

from playwright.sync_api import sync_playwright
from socket import AF_INET, socket, SOCK_STREAM
from threading import Thread

from scripts.login_screen import CONFIG
from scripts.utils import HiddenPrints

GAME_PATH = CONFIG["GAME_PATH"]
WEBUI_PATH = CONFIG["WEBUI_PATH"]
USE_TTS = CONFIG["USE_TTS"]
LAUNCH_YOURSELF = CONFIG["LAUNCH_YOURSELF"]
LAUNCH_YOURSELF_WEBUI = CONFIG["LAUNCH_YOURSELF_WEBUI"]
USE_ACTIONS = CONFIG["USE_ACTIONS"]
TTS_MODEL = CONFIG["TTS_MODEL"]
USE_SPEECH_RECOGNITION = CONFIG["USE_SPEECH_RECOGNITION"]
VOICE_SAMPLE_TORTOISE = CONFIG["VOICE_SAMPLE_TORTOISE"]
VOICE_SAMPLE_COQUI = CONFIG["VOICE_SAMPLE_COQUI"]

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Actions model
if USE_ACTIONS:
    try:
        from transformers import pipeline
    except ModuleNotFoundError:
        print("Please install transformers to use actions.")
        USE_ACTIONS = False
    with open("actions.yml", "r") as f:
        ACTIONS = yaml.safe_load(f)

    REVERT_ACTION_DICT = {}
    for key in ACTIONS:
        for action in ACTIONS[key]:
            REVERT_ACTION_DICT[action] = key
    ALL_ACTIONS = []
    for key in ACTIONS:
        ALL_ACTIONS += ACTIONS[key]

    action_classifier = pipeline(
        "zero-shot-classification",
        model="sileod/deberta-v3-base-tasksource-nli")

# TTS model
with HiddenPrints():
    if USE_TTS:
        from scripts.play_tts import play_TTS
        if TTS_MODEL == "Your TTS":
            from scripts.tts_api import my_TTS
            tts_model = my_TTS(model_name="tts_models/multilingual/multi-dataset/your_tts")
            sampling_rate = 16000
            voice_samples = None
            conditioning_latents = None
        elif TTS_MODEL == "Tortoise TTS":
            if device.type == "cuda":
                from tortoise.api_fast import TextToSpeech, MODELS_DIR
            else:
                from tortoise.api import TextToSpeech, MODELS_DIR
            from tortoise.utils.audio import load_voices
            from voicefixer import VoiceFixer
            tts_model = TextToSpeech(
                    models_dir=MODELS_DIR,
                    kv_cache=True,
                )
            voice_samples, conditioning_latents = load_voices([VOICE_SAMPLE_TORTOISE], ["tortoise_audios"])
            vfixer = VoiceFixer()
            sampling_rate = 24000
    else:
        print("No TTS model selected")


# Speech recognition model
if USE_SPEECH_RECOGNITION:
    try:
        import torch
    except ModuleNotFoundError:
        print("Please install torch to use speech recognition.")
        USE_SPEECH_RECOGNITION = False
    try:
        import speech_recognition as sr
    except ModuleNotFoundError:
        print("Please install SpeechRecognition to use speech recognition.")
        USE_SPEECH_RECOGNITION = False
    try:
        import whisper
    except ModuleNotFoundError:
        print("Please install whisper to use speech recognition.")
        USE_SPEECH_RECOGNITION = False
    try:
        import pyaudio
    except ModuleNotFoundError:
        print("Please install pyaudio to use speech recognition.")
        USE_SPEECH_RECOGNITION = False

    english = True

    def init_stt(model="base", english=True, energy=300, pause=0.8, dynamic_energy=False):
        if model != "large" and english:
            model = model + ".en"
        audio_model = whisper.load_model(model)
        r = sr.Recognizer()
        r.energy_threshold = energy
        r.pause_threshold = pause
        r.dynamic_energy_threshold = dynamic_energy
        return r, audio_model

    r, audio_model = init_stt()


# Chatbot connection
WEBUI_PATH = WEBUI_PATH.replace("\\", "/")
if not LAUNCH_YOURSELF_WEBUI:
    subprocess.Popen(WEBUI_PATH)
else:
    print("Please launch text-generation_webui manually.")
    print("Press enter to continue.")
    input()
    
def launch(context):
    page = context.new_page()
    page.goto("http://127.0.0.1:7860")
    page.wait_for_load_state("networkidle")
    context.storage_state(path="storage.json")
    return page


def post_message(page, message):
    if message == "QUIT":
        page.fill("[class='svelte-1f354aw pretty_scrollbar']", "I'll be right back")
    else:
        page.fill("[class='svelte-1f354aw pretty_scrollbar']", message)
    page.click('[id="Generate"]')
    page.wait_for_selector('[class="lg secondary svelte-cmf5ev hidden"]')


# Main
GAME_PATH = GAME_PATH.replace("\\", "/")
clients = {}
addresses = {}
HOST = '127.0.0.1'
PORT = 12346
BUFSIZE = 1024
ADDRESS = (HOST, PORT)
SERVER = socket(AF_INET, SOCK_STREAM)
SERVER.bind(ADDRESS)
queued = False
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

uni_chr_re = re.compile(r'\\u[0-9a-fA-F]{4}')

# Launch the game
if not LAUNCH_YOURSELF:
    subprocess.Popen(GAME_PATH+'/DDLC.exe')


def listen():
    print("Waiting for connection...")
    while True:
        client, client_address = SERVER.accept()
        print("%s:%s has connected." % client_address)
        addresses[client] = client_address
        Thread(target=call, args=(client,)).start()


def call(client):
    thread = Thread(target=listenToClient, args=(client,), daemon=True)
    thread.start()


def sendMessage(msg, name=""):
    """ send message to all users present in
    the chat room"""
    for client in clients:
        client.send(bytes(name, "utf8") + msg)


def send_answer(received_msg, msg):
    if received_msg != "" and USE_ACTIONS:
        sequence_to_classify = f"The player is speaking with Monika, his virtual \
            girlfriend. Now he says: {received_msg}. What is the label of this sentence?"
        action_to_take = action_classifier(sequence_to_classify, ALL_ACTIONS)
        action_to_take = action_to_take["labels"][0]
        print(action_to_take)
        print("Action: " + action_to_take)
        action_to_take = REVERT_ACTION_DICT[action_to_take]
    else:
        action_to_take = "none"
    action_to_take = action_to_take.encode("utf-8")
    emotion = "".encode("utf-8")
    msg = msg.encode("utf-8")
    """
    /g is used as a general separator between different elements in the message to send
    """
    msg_to_send = msg + b"/g" + emotion + b"/g" + action_to_take
    sendMessage(msg_to_send)


def listenToClient(client):
    """ Get client username """
    name = "User"
    clients[client] = name
    launched = False
    play_obj = None
    while True:
        try:
            received_msg = client.recv(BUFSIZE).decode("utf-8")
        except:
            print("Connection lost.")
            os._exit(0)
        received_msg = received_msg.split("/m")
        rest_msg = received_msg[1]
        received_msg = received_msg[0]
        if received_msg == "chatbot":
            if not launched:
                pw = sync_playwright().start()
                try:
                    browser = pw.firefox.launch(headless=False)
                    context = browser.new_context()
                    page = launch(context)
                except:
                    print("Launch failed. Please check if text-generation_webui is running.")
                    _ = client.recv(BUFSIZE).decode("utf-8")
                    sendMessage("server_error".encode("utf-8"))
                    launched = False
                    pw.stop()
                    continue
                launched = True
                _ = client.recv(BUFSIZE).decode("utf-8")
                sendMessage("server_ok".encode("utf-8"))

            if '/g' in rest_msg:
                received_msg, step = rest_msg.split("/g")
            else:
                received_msg = client.recv(BUFSIZE).decode("utf-8")  # Message containing the user input
                received_msg, step = received_msg.split("/g")
            step = int(step)
            
            try:
                post_message(page, received_msg)
            except:
                print("Error while sending message. Please check if text-generation_webui is running or if the model is loaded.")
                _ = client.recv(BUFSIZE).decode("utf-8")
                sendMessage("server_error".encode("utf-8"))
                launched = False
                pw.stop()
                continue
            if received_msg == "begin_record":
                if USE_SPEECH_RECOGNITION:
                    with sr.Microphone(sample_rate=16000) as source:
                        sendMessage("yes".encode("utf-8"))
                        audio = r.listen(source)
                        torch_audio = torch.from_numpy(
                            np.frombuffer(
                                audio.get_raw_data(),
                                np.int16
                            ).flatten().astype(np.float32) / 32768.0)
                        audio_data = torch_audio
                        if english:
                            result = audio_model.transcribe(audio_data, language='english')
                        else:
                            result = audio_model.transcribe(audio_data)
                        received_msg = result['text']
                else:
                    sendMessage("no".encode("utf-8"))
                    continue
            print("User: "+received_msg)

            while True:
                stop_button = page.locator('[class="lg secondary svelte-cmf5ev hidden"]')
                stop_button_style = stop_button.get_attribute("style")
                if "display: none;" in stop_button_style:
                    user = page.locator('[class="message-body"]').locator("nth=-1")
                    text = user.inner_html()
                    if len(text) > 0:
                        msg = text
                        msg = re.sub(r'<[^>]+>', '', msg)
                        msg = msg.replace('\n', '')
                        msg = os.linesep.join([s for s in msg.splitlines() if s])
                        msg = re.sub(' +', ' ', msg)
                        msg = re.sub(r'&[^;]+;', '', msg)
                        msg = msg.replace("END", "")
                    else:
                        continue
                    if received_msg != "QUIT":
                        if USE_TTS:
                            print("Using TTS")
                            play_obj = play_TTS(
                                step,
                                msg,
                                play_obj,
                                sampling_rate,
                                tts_model,
                                voice_samples,
                                conditioning_latents,
                                TTS_MODEL,
                                VOICE_SAMPLE_COQUI,
                                uni_chr_re)
                        print("Sent: " + msg)
                        send_answer(received_msg, msg)
                    break


if __name__ == "__main__":
    SERVER.listen(5)
    ACCEPT_THREAD = Thread(target=listen)
    ACCEPT_THREAD.start()
    ACCEPT_THREAD.join()
    SERVER.close()
