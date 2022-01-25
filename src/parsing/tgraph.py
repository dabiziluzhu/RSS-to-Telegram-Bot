import asyncio
import time
import aiographfix as aiograph
from typing import List, Union
from bs4 import BeautifulSoup
from aiohttp import ClientTimeout, ClientError
from aiohttp_retry import RetryClient
from aiohttp_socks import ProxyConnector

from src import env, log

logger = log.getLogger('RSStT.tgraph')


class Telegraph(aiograph.Telegraph):
    def __init__(self, token=None):
        self.last_run = 0
        self._fc_lock = asyncio.Lock()  # lock: wait if exceed flood control
        self._request_lock = asyncio.Lock()  # lock: only one request can be sent at the same time
        super().__init__(token)

    async def replace_session(self):
        await self.session.close()
        proxy_connector = ProxyConnector(**env.TELEGRAPH_PROXY_DICT, loop=self.loop) \
            if env.TELEGRAPH_PROXY_DICT else None
        self.session = RetryClient(connector=proxy_connector, timeout=ClientTimeout(total=10),
                                   loop=self.loop, json_serialize=self._json_serialize)

    async def create_page(self, *args, **kwargs) -> aiograph.types.Page:
        async with self._fc_lock:  # if not blocked, continue; otherwise, wait
            pass

        async with self._request_lock:
            await asyncio.sleep(max(0.5 - (time.time() - self.last_run), 0))  # avoid exceeding flood control
            page = await super().create_page(*args, **kwargs)
            self.last_run = time.time()
            return page

    async def flood_wait(self, retry_after: int):
        if not self._fc_lock.locked():  # if not already blocking
            async with self._fc_lock:  # block any other sending tries
                logger.info('Blocking any requests for this telegraph account due to flood control...')
                if retry_after >= 60:
                    # create a now account if retry_after sucks
                    await self.create_account(short_name='RSStT', author_name='Generated by RSStT',
                                              author_url='https://github.com/Rongronggg9/RSS-to-Telegram-Bot')
                    logger.warning('Wanna let me wait? No way! Created a new Telegraph account.')
                else:
                    await asyncio.sleep(retry_after + 1)
                logger.info('Unblocked.')


class APIs:
    def __init__(self, tokens: Union[str, List[str]]):
        if isinstance(tokens, str):
            tokens = [tokens]
        self.tokens = tokens
        self._accounts: List[Telegraph] = []
        self._curr_id = 0
        asyncio.get_event_loop().run_until_complete(self.init())

    async def init(self):
        for token in self.tokens:
            token = token.strip()
            account = Telegraph(token)
            await account.replace_session()
            try:
                if len(token) != 60:  # must be an invalid token
                    logger.warning('Telegraph API token may be invalid, create one instead.')
                    await account.create_account(short_name='RSStT', author_name='Generated by RSStT',
                                                 author_url='https://github.com/Rongronggg9/RSS-to-Telegram-Bot')
                await account.get_account_info()
                self._accounts.append(account)
            except aiograph.exceptions.TelegraphError as e:
                logger.warning('Telegraph API token may be invalid, create one instead: ' + str(e))
                try:
                    await account.create_account(short_name='RSStT', author_name='Generated by RSStT',
                                                 author_url='https://github.com/Rongronggg9/RSS-to-Telegram-Bot')
                    self._accounts.append(account)
                except Exception as e:
                    logger.warning('Cannot set up one of Telegraph accounts: ' + str(e), exc_info=e)
            except Exception as e:
                logger.warning('Cannot set up one of Telegraph accounts: ' + str(e), exc_info=e)

    @property
    def valid(self):
        return bool(self._accounts)

    @property
    def count(self):
        return len(self._accounts)

    def get_account(self) -> Telegraph:
        if not self._accounts:
            raise aiograph.exceptions.TelegraphError('Telegraph token no set!')

        curr_id = self._curr_id if 0 <= self._curr_id < len(self._accounts) else 0
        self._curr_id = curr_id + 1 if 0 <= curr_id + 1 < len(self._accounts) else 0
        return self._accounts[curr_id]


apis = None
if env.TELEGRAPH_TOKEN:
    apis = APIs(env.TELEGRAPH_TOKEN.split(','))
    if not apis.valid:
        logger.error('Cannot set up Telegraph, fallback to non-Telegraph mode.')
        apis = None

TELEGRAPH_ALLOWED_TAGS = {
    'a', 'aside', 'b', 'blockquote', 'br', 'code', 'em', 'figcaption', 'figure',
    'h3', 'h4', 'hr', 'i', 'iframe', 'img', 'li', 'ol', 'p', 'pre', 's',
    'strong', 'u', 'ul', 'video'
}

ALLOWED_ATTRS = {'href', 'src'}


class TelegraphIfy:
    def __init__(self, xml: str = None, title: str = None, link: str = None, feed_title: str = None,
                 author: str = None):
        self.retries = 0

        if not apis:
            raise aiograph.exceptions.TelegraphError('Telegraph token no set!')

        soup = BeautifulSoup(xml, 'lxml')

        for tag in soup.find_all(recursive=True):
            if tag.name not in TELEGRAPH_ALLOWED_TAGS:
                tag.replaceWithChildren()  # remove disallowed tags
                continue
            for attr in tuple(tag.attrs.keys()):
                if attr not in ALLOWED_ATTRS:
                    del tag.attrs[attr]  # remove disallowed attrs

        if feed_title:
            self.telegraph_author = f"{feed_title}"
            if author and author not in feed_title:
                self.telegraph_author += f' ({author})'
            self.telegraph_author_url = link if link else ''
        else:
            self.telegraph_author = 'Generated by RSStT'
            self.telegraph_author_url = 'https://github.com/Rongronggg9/RSS-to-Telegram-Bot'

        self.telegraph_title = title if title else 'Generated by RSStT'
        self.telegraph_html_content = (str(soup) +
                                       "<br><br>Generated by "
                                       "<a href='https://github.com/Rongronggg9/RSS-to-Telegram-Bot'>RSStT</a>. "
                                       "The copyright belongs to the source site.<br>"
                                       "If images cannot be loaded properly due to anti-hotlinking, "
                                       "please consider install "
                                       "<a href='https://greasyfork.org/scripts/432923'>this userscript</a>." +
                                       f"<br><br><a href='{link}'>Source</a>" if link else '')

    async def telegraph_ify(self):
        if self.retries >= 3:
            raise OverflowError

        if self.retries >= 1:
            logger.info('Retrying using another telegraph account...' if apis.count > 1 else 'Retrying...')

        telegraph_account = apis.get_account()
        try:
            telegraph_page = await telegraph_account.create_page(title=self.telegraph_title[:256],
                                                                 content=self.telegraph_html_content,
                                                                 author_name=self.telegraph_author[:128],
                                                                 author_url=self.telegraph_author_url[:512])
            return telegraph_page.url
        except aiograph.exceptions.TelegraphError as e:
            e_msg = str(e)
            if e_msg.startswith('FLOOD_WAIT_'):  # exceed flood control
                retry_after = int(e_msg.split('_')[-1])
                logger.warning(f'Flood control exceeded. Wait {retry_after}.0 seconds')
                self.retries += 1
                rets = await asyncio.gather(self.telegraph_ify(), telegraph_account.flood_wait(retry_after))

                return rets[0]
            else:
                raise e
        except (TimeoutError, asyncio.exceptions.TimeoutError) as e:
            raise e  # aiohttp_retry will retry automatically, so it means too many retries if caught
        except (ClientError, ConnectionError) as e:
            if self.retries < 3:
                logger.warning(
                    f'Network error ({e.__class__.__name__}) occurred when creating telegraph page, will retry')
                return await self.telegraph_ify()
            raise e
