# Chain Of Thought Prompting
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI


from openai.helpers import LocalAudioPlayer
import speech_recognition as sr


import json

import asyncio

from prompts import SYSTEM_PROMPT
from schemas import ModelOutputFormat
from tools import run_command

load_dotenv()

client = OpenAI()
async_client = AsyncOpenAI()

async def tts(speech: str):
    async with async_client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="nova",
        instructions="Always speak in cheerfull manner with full of delight and happy",
        input=speech,
        response_format="pcm",
    )as response:
        await LocalAudioPlayer().play(response)




available_tools = {
    "run_command": run_command
}


message_history = [
    { "role": "system", "content": SYSTEM_PROMPT },
]

r = sr.Recognizer() # Speech to Text
with sr.Microphone() as source: # Mic Access
    r.adjust_for_ambient_noise(source)
    r.pause_threshold = 2

    while True:
        print("Speak Something...")
        audio = r.listen(source)

        print("Processing Audio... (STT)")
        user_query = r.recognize_google(audio)
        message_history.append({ "role": "user", "content": user_query })

        while True:
            response = client.chat.completions.parse(
                model="gpt-4.1",
                response_format=ModelOutputFormat,
                messages=message_history
            )

            raw_result = response.choices[0].message.content
            message_history.append({"role": "assistant", "content": raw_result})
            
            parsed_result = response.choices[0].message.parsed

            if parsed_result.step == "START":
                print("🔥", parsed_result.content)
                continue

            if parsed_result.step == "TOOL":
                tool_to_call = parsed_result.tool
                tool_input = parsed_result.input
                print(f"🛠️: {tool_to_call} ({tool_input})")

                tool_response = available_tools[tool_to_call](tool_input)
                print(f"🛠️: {tool_to_call} ({tool_input}) = {tool_response}")
                message_history.append({ "role": "developer", "content": json.dumps(
                    { "step": "OBSERVE", "tool": tool_to_call, "input": tool_input, "output": tool_response}
                ) })
                continue



            if parsed_result.step == "PLAN":
                print("🧠", parsed_result.content)
                asyncio.run(tts(speech=parsed_result.content))
                continue

            if parsed_result.step == "OUTPUT":
                print("🤖", parsed_result.content)
                asyncio.run(tts(speech=parsed_result.content))
                break
