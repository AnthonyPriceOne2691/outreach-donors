/**
 * Очередь загрузки обхода — строкой на вкладке «Обход доноров» (29.10.2026). В неё же встают
 * доноры «Обойти всех по фильтру» сверх полусотни, и «Снять очередь» снимает и их.
 *
 * Доноры, загруженные списком с галочкой «в обход», ждут не в очереди обходов, а здесь
 * (`crawl/backlog.py`): сервис отдаёт их обходчикам по мере того, как те освобождаются, и
 * обход, запущенный кнопкой, идёт первым. Строка говорит, сколько ждёт и сколько это займёт
 * при нынешних обходчиках; пусто — строки нет. Снять очередь целиком может тот, у кого есть
 * право запуска, — с вопросом, потому что это тысячи обходов вперёд.
 */

import { Button, Flex, Group, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { BacklogView } from '../api/bulkDonors';
import { dropBacklog, fetchBacklog } from '../api/bulkDonors';
import { formatNumber, plural } from '../format';
import { notify } from '../notices';
import { spanWords } from './crawlWords';

export const BACKLOG_KEY = ['crawl-backlog'] as const;

/** Как часто спрашивать очередь — как сводка обхода: место освобождается в любую минуту. */
const POLL_MS = 10_000;
const DAY = 86_400;

/** Срок словами: от двух суток — сутками, короче — часами и минутами (`spanWords`). */
export function termWords(seconds: number): string {
  if (seconds >= 2 * DAY) return `${formatNumber(Math.round(seconds / DAY))} сут`;
  return spanWords(seconds);
}

/** «2 донора», «5 доноров». */
export function donorsWord(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'донор', 'донора', 'доноров')}`;
}

/** Сколько займёт очередь — или почему срок не назвать. */
export function etaWords(view: BacklogView): string {
  if (view.eta_sec === null || !view.workers) {
    return 'обходчиков сейчас не видно — обход начнётся, когда они появятся';
  }
  return (
    `≈ ${termWords(view.eta_sec)} при ${formatNumber(view.workers)} ` +
    `${plural(view.workers, 'обходчике', 'обходчиках', 'обходчиках')}`
  );
}

export function CrawlBacklog({ mayDrop }: { mayDrop: boolean }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const backlog = useQuery({
    queryKey: BACKLOG_KEY,
    queryFn: fetchBacklog,
    refetchInterval: POLL_MS,
  });
  const drop = useMutation({
    mutationFn: dropBacklog,
    onSuccess: (done) => {
      setAsking(false);
      notify({
        message: `Очередь загрузки снята: ${donorsWord(done.dropped)} в обход не ${plural(done.dropped, 'пойдёт', 'пойдут', 'пойдут')}.`,
        color: 'green',
      });
      void queryClient.invalidateQueries({ queryKey: BACKLOG_KEY });
    },
  });
  const view = backlog.data;
  if (view === undefined || view.waiting === 0) return null;
  return (
    // На телефоне кнопка — под строкой, а не сбоку: сбоку строке оставалась треть ширины.
    <Flex
      direction={{ base: 'column', xs: 'row' }}
      justify="space-between"
      align={{ base: 'flex-start', xs: 'center' }}
      gap="sm"
    >
      <Text size="sm">
        Очередь загрузки: {donorsWord(view.waiting)} {plural(view.waiting, 'ждёт', 'ждут', 'ждут')}{' '}
        обхода — {etaWords(view)}. Обходчики берут их по мере освобождения, обход, запущенный
        кнопкой, идёт первым.
      </Text>
      {/* Кнопка не ужимается: в строке вкладки длинные слова очереди обрезали её до «Снять оче…». */}
      {mayDrop ? (
        <Button
          variant="subtle"
          color="red"
          className="press"
          style={{ flexShrink: 0 }}
          onClick={() => setAsking(true)}
        >
          Снять очередь
        </Button>
      ) : null}
      <Modal opened={asking} onClose={() => setAsking(false)} title="Снять очередь загрузки?">
        <Stack gap="sm">
          <Text size="sm">
            {donorsWord(view.waiting)} в обход не{' '}
            {plural(view.waiting, 'пойдёт', 'пойдут', 'пойдут')}. Уже идущие обходы продолжатся,
            доноры и цены останутся — поставить их в обход можно кнопкой.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(false)}>
              Отмена
            </Button>
            <Button color="red" loading={drop.isPending} onClick={() => drop.mutate()}>
              Снять очередь
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Flex>
  );
}
