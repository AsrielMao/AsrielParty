"""
把词库导入数据库。

    python manage.py seed_words

可重复执行：已经存在的词对不会重复插入，所以改完 party/words.py 直接再跑一次即可。
"""

from django.core.management.base import BaseCommand

from party.models import UndercoverWordPair
from party.words import WORD_PAIRS


class Command(BaseCommand):
    help = '把 party/words.py 里的词库导入数据库（可重复执行）'

    def handle(self, *args, **options):
        created = 0
        for word_a, word_b in WORD_PAIRS:
            _, is_new = UndercoverWordPair.objects.get_or_create(word_a=word_a, word_b=word_b)
            if is_new:
                created += 1

        total = UndercoverWordPair.objects.count()
        self.stdout.write(self.style.SUCCESS('新增 %d 组，词库现在共 %d 组' % (created, total)))
