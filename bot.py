#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""bot.py

Main bot logic for handling voice conversations via Twilio Media Streams.
Uses OpenAI for LLM, Deepgram for STT, and Cartesia for TTS.
"""

import os
import sys
import json
from datetime import datetime   

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.openai import OpenAILLMService
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia import CartesiaTTSService
from pipecat.services.deepgram import DeepgramSTTService
from pipecat.frames.frames import TextFrame
from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

class Llm_logger(FrameProcessor):
    """Processor that logs user transcriptions to a text file."""
    
    def __init__(self, log_file="user_transcriptions.txt"):
        super().__init__()
        self.log_file = log_file
        
    async def process_frame(self, frame, direction):
        """Process frames and log transcriptions."""
        await super().process_frame(frame, direction)
        
        # Only log user transcriptions (from STT)
        if isinstance(frame, TextFrame):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            text = frame.text
            
            # Append to file
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {text}\n")
            
            logger.info(f"Logged transcription: {text}")
        
        # Pass frame along the pipeline
        await self.push_frame(frame, direction)

class Transcription_logger(FrameProcessor):
    """Processor that logs LLM transcriptions to a text file."""
    
    def __init__(self, log_file="llm_responses.txt"):
        super().__init__()
        self.log_file = log_file
        
    async def process_frame(self, frame, direction):
        """Process frames and log LLM responses.""" 
        await super().process_frame(frame, direction)
        
        # Only log LLM responses (from LLM)
        if isinstance(frame, TranscriptionFrame):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            text = frame.text
            
            # Append to file
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {text}\n")
            
            logger.info(f"Logged user response: {text}")
        
        # Pass frame along the pipeline
        await self.push_frame(frame, direction)


async def parse_twilio_stream_start(websocket):
    """Parse Twilio messages until we find the start event with call metadata."""
    try:
        # Keep reading messages until we get the "start" event
        max_attempts = 10
        for attempt in range(max_attempts):
            message = await websocket.receive_text()
            data = json.loads(message)
            
            event_type = data.get("event", "unknown")
            logger.debug(f"Received Twilio message: {event_type}")
            
            if event_type == "start":
                # Found it! Extract the data
                start_data = data.get("start", {})
                stream_sid = data.get("streamSid", "")
                call_sid = start_data.get("callSid", "")
                
                custom_params = start_data.get("customParameters", {})
                to_number = custom_params.get("to_number", "Unknown")
                from_number = custom_params.get("from_number", "Unknown")
                
                logger.info(f"Stream started - Call SID: {call_sid}")
                return {
                    "stream_id": stream_sid,
                    "call_id": call_sid,
                    "to_number": to_number,
                    "from_number": from_number
                }
            elif event_type == "connected":
                # This is expected, just continue to next message
                continue
            else:
                logger.warning(f"Unexpected event: {event_type}")
                
        # If we get here, we never found "start"
        raise Exception("Never received 'start' event from Twilio")
        
    except Exception as e:
        logger.error(f"Error parsing Twilio stream: {e}")
        raise

async def run_bot(transport: BaseTransport, handle_sigint: bool):
    """Initialize and run the conversational AI bot pipeline.
    
    Args:
        transport: The transport layer for audio I/O
        handle_sigint: Whether to handle SIGINT signals
    """
    # Initialize OpenAI LLM Service
    # Available models: "gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"
    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o"  # Using the latest optimized model
    )

    # Initialize Speech-to-Text service
    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))

    # Initialize Text-to-Speech service
    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id="71a7ad14-091c-4e8e-a314-022ece01c121",  # British Reading Lady
    )

    # Define the system prompt and initial messages
    messages = [
        {
            "role": "system",
            "content": (
                "your name is khushee, you are an expert in woman's health and wellness. you will respond in a nice and friendly manner. you will keep your responses concise and to the point, and you will laugh whenever possible. Educate the person on healthy habits and lifestyle choices. The user will ask about ipill and birth control options. You will answer in a succinct and friendly manner."
            ),
        },
    ]

    # Initialize OpenAI-specific context
    context = OpenAILLMContext(messages)
    
    # Create context aggregator using the LLM service
    context_aggregator = llm.create_context_aggregator(context)

    llm_logger = Llm_logger(log_file="user_transcriptions.txt")

    transcription_logger = Transcription_logger(log_file="user_transcriptions.txt")


    # Build the processing pipeline
    pipeline = Pipeline(
        [
            transport.input(),              # Websocket input from Twilio
            stt,         
            transcription_logger,                     # STT processing (Deepgram) 
            context_aggregator.user(),      # Aggregate user messages
            llm,    
            llm_logger,                                                # LLM processing (OpenAI)
            tts,                            # Text-To-Speech (Cartesia)
            transport.output(),             # Websocket output to Twilio
            context_aggregator.assistant(), # Aggregate assistant messages
        ]
    )

    # Create pipeline task with audio parameters
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,   # Twilio uses 8kHz
            audio_out_sample_rate=8000,  # Twilio uses 8kHz
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        """Handle client connection event."""
        logger.info("Client connected - starting outbound call conversation with OpenAI")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        """Handle client disconnection event."""
        logger.info("Client disconnected - outbound call ended")
        await task.cancel()

    # Run the pipeline
    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args):
    """Main bot entry point compatible with WebSocket runner.
    
    Args:
        runner_args: WebSocketRunnerArguments containing websocket and configuration
    """
    logger.info("Bot started - parsing Twilio stream")
    
    # Parse the Twilio stream start message to get call metadata
    call_data = await parse_twilio_stream_start(runner_args.websocket)
    
    stream_id = call_data.get("stream_id", "")
    call_id = call_data.get("call_id", "")
    to_number = call_data.get("to_number", "Unknown")
    from_number = call_data.get("from_number", "Unknown")

    logger.info(f"Call metadata - To: {to_number}, From: {from_number}")

    # Initialize Twilio frame serializer
    serializer = TwilioFrameSerializer(
        stream_sid=stream_id,
        call_sid=call_id,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
    )

    # Initialize WebSocket transport with Twilio-specific configuration
    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,           # Twilio doesn't need WAV headers
            vad_analyzer=SileroVADAnalyzer(), # Voice Activity Detection
            serializer=serializer,
        ),
    )

    # Get signal handling preference from runner args
    handle_sigint = getattr(runner_args, 'handle_sigint', False)

    # Start the bot
    await run_bot(transport, handle_sigint)