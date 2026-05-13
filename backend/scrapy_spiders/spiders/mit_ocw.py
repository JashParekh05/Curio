import scrapy
import re


class MITOCWSpider(scrapy.Spider):
    """
    Crawls MIT OpenCourseWare for lecture video URLs.
    Run with: scrapy crawl mit_ocw -a subject=computer-science
    """

    name = "mit_ocw"
    allowed_domains = ["ocw.mit.edu"]
    start_urls = ["https://ocw.mit.edu/search/?d=Electrical+Engineering+and+Computer+Science&f=Video"]

    def parse(self, response):
        course_links = response.css("a.course-link::attr(href), a[href*='/courses/']::attr(href)").getall()
        for link in set(course_links):
            yield response.follow(link, callback=self.parse_course)

        next_page = response.css("a[rel='next']::attr(href)").get()
        if next_page:
            yield response.follow(next_page, callback=self.parse)

    def parse_course(self, response):
        video_page_links = response.css(
            "a[href*='/lecture-videos/']::attr(href), "
            "a[href*='/video-lectures/']::attr(href)"
        ).getall()
        for link in video_page_links:
            yield response.follow(link, callback=self.parse_video_page)

    def parse_video_page(self, response):
        youtube_url = response.css(
            "iframe[src*='youtube']::attr(src)"
        ).get()

        if not youtube_url:
            return

        if "embed/" in youtube_url:
            video_id = youtube_url.split("embed/")[1].split("?")[0]
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"

        yield {
            "source": "mit_ocw",
            "source_url": response.url,
            "youtube_url": youtube_url,
            "title": response.css("h1::text").get("").strip(),
            "description": response.css("meta[name='description']::attr(content)").get(""),
            "course": response.url.split("/courses/")[1].split("/")[0] if "/courses/" in response.url else "",
        }
