import sys
import types
import unittest
from unittest.mock import patch

from app.intelligence_desk.tools import company_scout_tools as tools_module


TARGET = {
    "website_url": "https://company.example/",
    "youtube_url": "https://www.youtube.com/@company",
}


class CompanyScoutToolTests(unittest.TestCase):
    def make_tools(self):
        with patch.object(tools_module, "_target_snapshot", return_value=TARGET):
            return tools_module.build_company_scout_tools(10)

    def test_factory_exposes_only_three_tools(self):
        tools = self.make_tools()
        self.assertEqual(
            [item.name for item in tools],
            ["get_weekly_youtube_videos", "get_youtube_transcript", "read_webpage"],
        )

    def test_weekly_video_tool_filters_by_kolkata_week(self):
        youtube = self.make_tools()[0]
        responses = [
            {"items": [{"id": "UC-company", "contentDetails": {"relatedPlaylists": {"uploads": "uploads"}}}]},
            {"items": [
                {"contentDetails": {"videoId": "inside"}},
                {"contentDetails": {"videoId": "outside"}},
            ]},
            {"items": [
                {"id": "inside", "snippet": {
                    "channelId": "UC-company", "title": "Inside", "description": "Week update",
                    "publishedAt": "2026-11-16T00:00:00Z",
                }},
                {"id": "outside", "snippet": {
                    "channelId": "UC-company", "title": "Outside", "description": "Next week",
                    "publishedAt": "2026-11-23T00:00:00Z",
                }},
            ]},
        ]
        with patch.dict("os.environ", {"YOUTUBE_API_KEY": "test-key"}), patch.object(
            tools_module, "_youtube_request", side_effect=responses
        ):
            result = youtube.func("2026-11-16", "2026-11-22")

        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["video_id"], "inside")

    def test_weekly_video_tool_requires_api_key(self):
        youtube = self.make_tools()[0]
        with patch.dict("os.environ", {}, clear=True):
            result = youtube.func("2026-11-16", "2026-11-22")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["diagnostic"], "youtube_api_key_missing")

    def test_transcript_requires_video_from_weekly_video_tool(self):
        transcript = self.make_tools()[1]
        result = transcript.func("unrecognized-id")
        self.assertEqual(result["status"], "invalid_input")

    def test_transcript_returns_available_language(self):
        video_tool, transcript_tool, _ = self.make_tools()
        api_responses = [
            {"items": [{"id": "UC-company", "contentDetails": {"relatedPlaylists": {"uploads": "uploads"}}}]},
            {"items": [{"contentDetails": {"videoId": "video12345"}}]},
            {"items": [{"id": "video12345", "snippet": {
                "channelId": "UC-company", "title": "Video", "description": "",
                "publishedAt": "2026-11-17T12:00:00Z",
            }}]},
        ]

        class Track:
            language = "Hindi"
            language_code = "hi"

            def fetch(self):
                return [types.SimpleNamespace(text="नमस्ते")]

        class TranscriptApi:
            def list(self, video_id):
                return [Track()]

        fake_module = types.ModuleType("youtube_transcript_api")
        fake_module.YouTubeTranscriptApi = TranscriptApi
        with patch.dict("os.environ", {"YOUTUBE_API_KEY": "test-key"}), patch.object(
            tools_module, "_youtube_request", side_effect=api_responses
        ), patch.dict(sys.modules, {"youtube_transcript_api": fake_module}):
            videos = video_tool.func("2026-11-16", "2026-11-22")
            result = transcript_tool.func("video12345")

        self.assertEqual(videos["count"], 1)
        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["language_code"], "hi")
        self.assertEqual(result["text"], "नमस्ते")

    def test_webpage_returns_text_and_links_and_allows_returned_link(self):
        webpage = self.make_tools()[2]
        pages = [
            ("https://company.example/", b'<html><head><title>Company</title><meta property="article:published_time" content="2026-11-18"></head><body><p>Company update</p><a href="https://news.example/post">Announcement</a></body></html>', "utf-8"),
            ("https://news.example/post", b"<html><title>Announcement</title><body>Details here</body></html>", "utf-8"),
        ]
        with patch.object(tools_module, "_fetch_html", side_effect=pages), patch.object(
            tools_module, "_safe_http_url", return_value=True
        ):
            first = webpage.func("https://company.example/")
            second = webpage.func("https://news.example/post")

        self.assertEqual(first["status"], "checked")
        self.assertIn("Company update", first["text"])
        self.assertEqual(first["published_at"], "2026-11-18")
        self.assertEqual(first["links"][0]["url"], "https://news.example/post")
        self.assertEqual(second["status"], "checked")

    def test_webpage_rejects_url_not_found_in_page_links(self):
        webpage = self.make_tools()[2]
        result = webpage.func("https://other.example/")
        self.assertEqual(result["status"], "rejected")

    def test_webpage_rejects_dropped_social_sources(self):
        self.assertFalse(tools_module._safe_http_url("https://instagram.com/company"))
        self.assertFalse(tools_module._safe_http_url("https://x.com/company"))

    def test_week_window_must_be_monday_through_sunday(self):
        with self.assertRaises(ValueError):
            tools_module._week_bounds("2026-11-17", "2026-11-23")


if __name__ == "__main__":
    unittest.main()
