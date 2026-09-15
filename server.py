import base64
import hashlib
import json
import os
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
                    descriptions[image_id] = {
                        "description": description,
                        "image_hash": image_hash,
                    }
    return descriptions


description_cache = load_descriptions()


def save_descriptions():
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
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/descriptions":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(json.dumps(description_cache).encode("utf-8"))
            return
        elif self.path.startswith("/images/"):
            self.send_response(200)
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            with open("." + self.path, "rb") as f:
                self.wfile.write(f.read())
            return
        super().do_GET()

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
        except (ValueError, json.JSONDecodeError, OSError) as error:
            self.send_json(400, {"error": str(error) or "A valid image is required."})
            return

        image_hash = hashlib.sha256(image_bytes).hexdigest()
        cached_entry = description_cache.get(image_id)
        if (
            cached_entry
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
                "temperature": 0.2,
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
            description_cache[image_id] = {
                "description": description,
                "image_hash": image_hash,
            }
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