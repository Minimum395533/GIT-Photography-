import base64
import hashlib
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PORT = 5000
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL = "qwen-3.8-27b"
MAX_REQUEST_BYTES = 10 * 1024 * 1024
DESCRIPTIONS_FILE = Path(__file__).with_name("descriptions.json")
IMAGE_IDS = {f"image-{index}" for index in range(1, 10)}


def load_descriptions():
    try:
        with DESCRIPTIONS_FILE.open("r", encoding="utf-8") as file:
            saved = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        saved = {}

    descriptions = {}
    if isinstance(saved, dict):
        for image_id in IMAGE_IDS:
            entry = saved.get(image_id)
            if isinstance(entry, dict):
                description = entry.get("description", "")
                image_hash = entry.get("image_hash", "")
                if isinstance(description, str) and isinstance(image_hash, str):
                    normalized_entry = {
                        "description": description,
                        "image_hash": image_hash,
                    }
                    background_color = entry.get("background_color")
                    if (
                        isinstance(background_color, str)
                        and len(background_color) == 2
                        and all(character in "0123456789abcdefABCDEF" for character in background_color)
                    ):
                        normalized_entry["background_color"] = background_color.upper()
                    position = entry.get("position")
                    if (
                        isinstance(position, list)
                        and len(position) == 2
                        and all(
                            isinstance(value, int)
                            and not isinstance(value, bool)
                            and 0 <= value <= 1000
                            for value in position
                        )
                    ):
                        normalized_entry["position"] = position
                    position_limits = entry.get("position_limits")
                    if (
                        isinstance(position_limits, list)
                        and len(position_limits) == 4
                        and all(isinstance(value, int) and not isinstance(value, bool) for value in position_limits)
                        and position_limits[0] <= position_limits[1]
                        and position_limits[2] <= position_limits[3]
                    ):
                        normalized_entry["position_limits"] = position_limits
                    descriptions[image_id] = normalized_entry
    return descriptions


description_cache = load_descriptions()
description_save_lock = threading.RLock()


def save_descriptions():
    with description_save_lock:
        temporary_file = DESCRIPTIONS_FILE.with_suffix(".json.tmp")
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(description_cache, file, indent=2, ensure_ascii=False)
            file.write("\n")
        temporary_file.replace(DESCRIPTIONS_FILE)


def content_to_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            text for text in (content_to_text(item) for item in content) if text
        ).strip()
    if isinstance(content, dict):
        for key in ("text", "content", "generated_text", "message"):
            if key in content:
                text = content_to_text(content[key])
                if text:
                    return text
    return str(content).strip() if content is not None else ""


def decode_image_data(image_data):
    if not isinstance(image_data, str) or not image_data.startswith("data:image/"):
        raise ValueError("The selected image must be provided as an image data URI.")

    try:
        metadata, encoded = image_data.split(",", 1)
        if ";base64" not in metadata:
            raise ValueError
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError, base64.binascii.Error) as error:
        raise ValueError("The selected image data is invalid.") from error

    if not decoded or len(decoded) > MAX_REQUEST_BYTES:
        raise ValueError("The selected image is too large.")
    return decoded


class GalleryRequestHandler(SimpleHTTPRequestHandler):
    def send_json(self, status, payload, cache_control=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        request_path = self.path.split("?", 1)[0]
        if request_path.startswith("/images/"):
            self.send_header(
                "Cache-Control",
                "public, max-age=31536000, immutable",
            )
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/descriptions":
            self.send_json(200, description_cache, "no-cache")
            return
        super().do_GET()

    def do_DELETE(self):
        if self.path != "/api/descriptions":
            self.send_json(404, {"error": "Not found."})
            return

        previous_descriptions = description_cache.copy()
        description_cache.clear()
        for image_id, entry in previous_descriptions.items():
            background_color = entry.get("background_color")
            position = entry.get("position")
            position_limits = entry.get("position_limits")
            if background_color or position or position_limits:
                preserved_entry = {
                    "description": "",
                    "image_hash": "",
                }
                if background_color:
                    preserved_entry["background_color"] = background_color
                if position:
                    preserved_entry["position"] = position
                if position_limits:
                    preserved_entry["position_limits"] = position_limits
                description_cache[image_id] = preserved_entry
        try:
            save_descriptions()
        except OSError:
            description_cache.update(previous_descriptions)
            self.send_json(500, {"error": "Saved descriptions could not be deleted."})
            return

        self.send_json(
            200,
            {"deleted": len(previous_descriptions), "descriptions": description_cache},
        )

    def do_PUT(self):
        if self.path == "/api/image-position":
            self.save_image_position()
            return
        if self.path != "/api/background-color":
            self.send_json(404, {"error": "Not found."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 1024:
                raise ValueError("The request is invalid.")
            payload = json.loads(self.rfile.read(content_length))
            image_id = payload.get("imageId", "")
            background_color = payload.get("backgroundColor", "")
            if image_id not in IMAGE_IDS:
                raise ValueError("A valid image ID is required.")
            if (
                not isinstance(background_color, str)
                or len(background_color) != 2
                or not all(character in "0123456789abcdefABCDEF" for character in background_color)
            ):
                raise ValueError("Background color must be a two-digit hex value.")
            background_color = background_color.upper()
        except (ValueError, json.JSONDecodeError, OSError) as error:
            self.send_json(400, {"error": str(error) or "A valid background color is required."})
            return

        entry = description_cache.get(
            image_id,
            {"description": "", "image_hash": ""},
        )
        entry["background_color"] = background_color
        description_cache[image_id] = entry
        try:
            save_descriptions()
        except OSError:
            self.send_json(500, {"error": "The background color could not be saved."})
            return

        self.send_json(
            200,
            {"imageId": image_id, "backgroundColor": background_color},
        )

    def save_image_position(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 1024:
                raise ValueError("The request is invalid.")
            payload = json.loads(self.rfile.read(content_length))
            image_id = payload.get("imageId", "")
            position = payload.get("position")
            position_limits = payload.get("positionLimits")
            if image_id not in IMAGE_IDS:
                raise ValueError("A valid image ID is required.")
            if (
                not isinstance(position, list)
                or len(position) != 2
                or not all(
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 1000
                    for value in position
                )
            ):
                raise ValueError("Position must contain two values between 0 and 1000.")
            if (
                not isinstance(position_limits, list)
                or len(position_limits) != 4
                or not all(isinstance(value, int) and not isinstance(value, bool) for value in position_limits)
                or position_limits[0] > position_limits[1]
                or position_limits[2] > position_limits[3]
            ):
                raise ValueError("Position limits are invalid.")
        except (ValueError, json.JSONDecodeError, OSError) as error:
            self.send_json(400, {"error": str(error) or "A valid image position is required."})
            return

        entry = description_cache.get(
            image_id,
            {"description": "", "image_hash": ""},
        )
        entry["position"] = position
        entry["position_limits"] = position_limits
        description_cache[image_id] = entry
        try:
            save_descriptions()
        except OSError:
            self.send_json(500, {"error": "The image position could not be saved."})
            return

        self.send_json(
            200,
            {
                "imageId": image_id,
                "position": position,
                "positionLimits": position_limits,
            },
        )

    def do_POST(self):
        if self.path != "/api/generate-description":
            self.send_json(404, {"error": "Not found."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("The request is too large.")
            payload = json.loads(self.rfile.read(content_length))
            image_id = payload.get("imageId", "")
            if image_id not in IMAGE_IDS:
                raise ValueError("A valid image ID is required.")
            image_data_uri = payload.get("image", "")
            image_bytes = decode_image_data(image_data_uri)
            regenerate = payload.get("regenerate") is True
            temperature = payload.get("temperature", 0.2)
            seed = payload.get("seed")
            top_p = payload.get("top_p", 0.9)
            if (
                isinstance(temperature, bool)
                or not isinstance(temperature, (int, float))
                or not 0 <= temperature <= 2
            ):
                raise ValueError("Temperature must be between 0 and 2.")
            if seed is not None and (
                isinstance(seed, bool)
                or not isinstance(seed, int)
                or not 0 <= seed <= 2147483647
            ):
                raise ValueError("Seed must be an integer between 0 and 2147483647.")
            if (
                isinstance(top_p, bool)
                or not isinstance(top_p, (int, float))
                or not 0 < top_p <= 1
            ):
                raise ValueError("Top-p must be greater than 0 and no more than 1.")
        except (ValueError, json.JSONDecodeError, OSError) as error:
            self.send_json(400, {"error": str(error) or "A valid image is required."})
            return

        image_hash = hashlib.sha256(image_bytes).hexdigest()
        cached_entry = description_cache.get(image_id)
        if (
            not regenerate
            and cached_entry
            and cached_entry.get("image_hash") == image_hash
            and cached_entry.get("description")
        ):
            self.send_json(
                200,
                {
                    "imageId": image_id,
                    "description": cached_entry["description"],
                    "cached": True,
                },
            )
            return

        api_key = os.environ.get("CEREBRAS_API_KEY")
        if not api_key:
            self.send_json(503, {"error": "Cerebras is not configured on the server."})
            return

        generation_options = {
            "temperature": temperature,
            "seed": seed,
            "top_p": top_p,
        }
        request_payload = json.dumps(
            {
                "model": CEREBRAS_MODEL,
                "reasoning_effort": "none",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Describe this image in one concise sentence.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": image_data_uri},
                            },
                        ],
                    }
                ],
                "max_completion_tokens": 80,
                **generation_options,
            }
        ).encode("utf-8")
        request = Request(
            CEREBRAS_API_URL,
            data=request_payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "PhotographyGallery/1.0",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=60) as response:
                response_data = json.loads(response.read())
            choices = response_data.get("choices", [])
            message = choices[0].get("message", {}) if choices else {}
            description = content_to_text(message.get("content", ""))
            if not description:
                raise ValueError("Cerebras returned an empty description.")
            updated_entry = {
                "description": description,
                "image_hash": image_hash,
            }
            existing_entry = description_cache.get(image_id, {})
            if existing_entry.get("background_color"):
                updated_entry["background_color"] = existing_entry["background_color"]
            description_cache[image_id] = updated_entry
            try:
                save_descriptions()
            except OSError:
                self.send_json(
                    500,
                    {"error": "The description was generated but could not be cached."},
                )
                return
            self.send_json(
                200,
                {"imageId": image_id, "description": description, "cached": False},
            )
        except HTTPError as error:
            try:
                error_payload = json.loads(error.read())
                provider_message = content_to_text(
                    error_payload.get("error", error_payload.get("message", ""))
                    if isinstance(error_payload, dict)
                    else error_payload
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                provider_message = ""
            self.send_json(
                error.code,
                {"error": provider_message or "Cerebras returned an error."},
            )
        except (URLError, TimeoutError):
            self.send_json(502, {"error": "Unable to reach Cerebras right now."})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(502, {"error": str(error)})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), GalleryRequestHandler)
    print(f"Serving photography gallery on 0.0.0.0:{PORT}")
    server.serve_forever()