import scrapy


class KhanAcademySpider(scrapy.Spider):
    """
    Crawls Khan Academy subject pages to extract video URLs and metadata.
    Run with: scrapy crawl khan_academy -a subject=math
    """

    name = "khan_academy"
    allowed_domains = ["khanacademy.org"]

    SUBJECT_URLS = {
        "math": "https://www.khanacademy.org/math",
        "cs": "https://www.khanacademy.org/computing/computer-science",
        "biology": "https://www.khanacademy.org/science/biology",
        "chemistry": "https://www.khanacademy.org/science/chemistry",
        "physics": "https://www.khanacademy.org/science/physics",
        "economics": "https://www.khanacademy.org/economics-finance-domain",
    }

    def __init__(self, subject="cs", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [self.SUBJECT_URLS.get(subject, self.SUBJECT_URLS["cs"])]
        self.subject = subject

    def parse(self, response):
        # Extract course/unit links from the subject page
        course_links = response.css("a[href*='/course/']::attr(href), a[href*='/unit/']::attr(href)").getall()
        for link in course_links:
            yield response.follow(link, callback=self.parse_course)

    def parse_course(self, response):
        # Extract lesson links
        lesson_links = response.css("a[href*='/v/']::attr(href)").getall()
        for link in lesson_links:
            yield response.follow(link, callback=self.parse_video)

    def parse_video(self, response):
        # Extract YouTube embed URL from Khan Academy video page
        youtube_url = response.css(
            "iframe[src*='youtube']::attr(src), "
            "iframe[data-src*='youtube']::attr(data-src)"
        ).get()

        if not youtube_url:
            return

        # Convert embed URL to watch URL
        if "embed/" in youtube_url:
            video_id = youtube_url.split("embed/")[1].split("?")[0]
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"

        yield {
            "source": "khan_academy",
            "source_url": response.url,
            "youtube_url": youtube_url,
            "title": response.css("h1::text, title::text").get("").strip(),
            "description": response.css("meta[name='description']::attr(content)").get(""),
            "subject": self.subject,
        }
