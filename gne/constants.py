"""Static defaults used by the news extraction engine."""

import re

AUTHOR_PATTERN_STR = [
    r"责编[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：]",
    r"责任编辑[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：]",
    r"作者[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：]",
    r"编辑[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：]",
    r"文[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：]",
    r"原创[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：]",
    r"撰文[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：]",
    r"来源[：: 丨/]\s*([\u4E00-\u9FA5a-zA-Z0-9][\u4E00-\u9FA5a-zA-Z]{1,19})[^\u4E00-\u9FA5:：<]",
]

AUTHOR_PATTERN = [re.compile(pattern) for pattern in AUTHOR_PATTERN_STR]

DATETIME_PATTERN_STR = [
    r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[0-1]?[0-9]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[2][0-3]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[0-1]?[0-9]:[0-5]?[0-9])",
    r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[2][0-3]:[0-5]?[0-9])",
    r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[1-24]\d时[0-60]\d分)([1-24]\d时)",
    r"(\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[0-1]?[0-9]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[2][0-3]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[0-1]?[0-9]:[0-5]?[0-9])",
    r"(\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[2][0-3]:[0-5]?[0-9])",
    r"(\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*?[1-24]\d时[0-60]\d分)([1-24]\d时)",
    r"(\d{4}年\d{1,2}月\d{1,2}日\s*?[0-1]?[0-9]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{4}年\d{1,2}月\d{1,2}日\s*?[2][0-3]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{4}年\d{1,2}月\d{1,2}日\s*?[0-1]?[0-9]:[0-5]?[0-9])",
    r"(\d{4}年\d{1,2}月\d{1,2}日\s*?[2][0-3]:[0-5]?[0-9])",
    r"(\d{4}年\d{1,2}月\d{1,2}日\s*?[1-24]\d时[0-60]\d分)([1-24]\d时)",
    r"(\d{2}年\d{1,2}月\d{1,2}日\s*?[0-1]?[0-9]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{2}年\d{1,2}月\d{1,2}日\s*?[2][0-3]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{2}年\d{1,2}月\d{1,2}日\s*?[0-1]?[0-9]:[0-5]?[0-9])",
    r"(\d{2}年\d{1,2}月\d{1,2}日\s*?[2][0-3]:[0-5]?[0-9])",
    r"(\d{2}年\d{1,2}月\d{1,2}日\s*?[1-24]\d时[0-60]\d分)([1-24]\d时)",
    r"(\d{1,2}月\d{1,2}日\s*?[0-1]?[0-9]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{1,2}月\d{1,2}日\s*?[2][0-3]:[0-5]?[0-9]:[0-5]?[0-9])",
    r"(\d{1,2}月\d{1,2}日\s*?[0-1]?[0-9]:[0-5]?[0-9])",
    r"(\d{1,2}月\d{1,2}日\s*?[2][0-3]:[0-5]?[0-9])",
    r"(\d{1,2}月\d{1,2}日\s*?[1-24]\d时[0-60]\d分)([1-24]\d时)",
    r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})",
    r"(\d{2}[-/.]\d{1,2}[-/.]\d{1,2})",
    r"(\d{4}年\d{1,2}月\d{1,2}日)",
    r"(\d{2}年\d{1,2}月\d{1,2}日)",
    r"(\d{1,2}月\d{1,2}日)",
]

DATETIME_PATTERN = [re.compile(pattern) for pattern in DATETIME_PATTERN_STR]

TITLE_HTAG_XPATH = "//h1//text() | //h2//text() | //h3//text() | //h4//text()"
TITLE_HTAG_AND_TITLE_XPATH = (
    "(//h1//text() | //h2//text() | //h3//text() | //h4//text() | //h5//text())"
)
TITLE_SPLIT_CHAR_PATTERN = "[-_|]"

USELESS_TAG = [
    "style",
    "script",
    "link",
    "video",
    "iframe",
    "source",
    "picture",
    "header",
    "footer",
    "nav",
    "aside",
]

# 这些标签在没有任何子节点或文本时可以被安全移除。
TAGS_CAN_BE_REMOVE_IF_EMPTY = ["section", "h1", "h2", "h3", "h4", "h5", "h6", "span"]

USELESS_ATTR = {
    "share",
    "sharing",
    "contribution",
    "copyright",
    "copy-right",
    "disclaimer",
    "recommend",
    "related",
    "footer",
    "comment",
    "social",
    "submeta",
    "report-infor",
    "header_toolbar",
    "sidebar",
    "navigation",
    "nav",
    "breadcrumb",
    "ad",
    "advertisement",
    "newsletter",
    "signup",
    "subscribe",
    "interaction",
    "donate",
    "popup",
    "modal",
    "toolbar",
    "promo",
}

HIGH_WEIGHT_ATTR_KEYWORD = [
    "content",
    "article",
    "news_txt",
    "pages_content",
    "post_text",
    "post_body",
    "entry-content",
    "article-body",
    "story-body",
    "main-content",
    "articlebody",
    "article-text",
    "story-contents",
    "post-content",
    "post_content",
    "news-content",
]

# 保留旧名称，避免外部代码直接导入时断裂。
HIGH_WEIGHT_ARRT_KEYWORD = HIGH_WEIGHT_ATTR_KEYWORD

PUBLISH_TIME_META = [
    '//meta[starts-with(@property, "rnews:datePublished")]/@content',
    '//meta[starts-with(@property, "article:published_time")]/@content',
    '//meta[starts-with(@property, "article:publish_time")]/@content',
    '//meta[starts-with(@property, "og:published_time")]/@content',
    '//meta[starts-with(@property, "og:release_date")]/@content',
    '//meta[starts-with(@property, "og:pubdate")]/@content',
    '//meta[starts-with(@itemprop, "datePublished")]/@content',
    '//meta[starts-with(@itemprop, "dateUpdate")]/@content',
    '//meta[starts-with(@name, "OriginalPublicationDate")]/@content',
    '//meta[starts-with(@name, "article_date_original")]/@content',
    '//meta[starts-with(@name, "og:time")]/@content',
    '//meta[starts-with(@name, "apub:time")]/@content',
    '//meta[starts-with(@name, "publication_date")]/@content',
    '//meta[starts-with(@name, "firstpublishedtime")]/@content',
    '//meta[starts-with(@name, "firstPublishedTime")]/@content',
    '//meta[starts-with(@name, "sailthru.date")]/@content',
    '//meta[starts-with(@name, "PublishDate")]/@content',
    '//meta[starts-with(@name, "publishdate")]/@content',
    '//meta[starts-with(@name, "PubDate")]/@content',
    '//meta[starts-with(@name, "pubtime")]/@content',
    '//meta[starts-with(@name, "_pubtime")]/@content',
    '//meta[starts-with(@name, "weibo: article:create_at")]/@content',
    '//meta[starts-with(@pubdate, "pubdate")]/@content',
    '//meta[starts-with(@name, "date")]/@content',
    '//meta[starts-with(@name, "DC.date")]/@content',
    '//meta[starts-with(@name, "DC.date.issued")]/@content',
    '//meta[starts-with(@name, "dcterms.date")]/@content',
    '//meta[starts-with(@name, "dcterms.created")]/@content',
    '//meta[starts-with(@name, "parsely-pub-date")]/@content',
    '//meta[starts-with(@name, "date_published")]/@content',
    '//meta[starts-with(@name, "published_at")]/@content',
    '//meta[starts-with(@name, "publish-date")]/@content',
    '//meta[starts-with(@name, "published-date")]/@content',
    '//meta[starts-with(@name, "shareaholic:article_published_time")]/@content',
    '//meta[starts-with(@name, "cXenseParse:recs:publishtime")]/@content',
    '//meta[starts-with(@name, "datepublished")]/@content',
    '//meta[starts-with(@property, "bt:pubDate")]/@content',
]

# 满足这些 XPath 的页面很可能是文章详情页。
ARTICLE_XPATH = [
    '//*[@class="article__content"]',
]
