/**
 * «Ящики и разгон» этапа — под его доменами: ступень сейчас, следующая ступень и почему ящик сегодня
 * не берёт писем. В строке домена этого нет: там итог домена и кнопка.
 *
 * **Блок один на все этапы** (`MailingBoxes`, общий прогноз почты). С панели рекламодателей и вкладки
 * «Рассылка» продаж он переехал сюда (просьба Anthony 05.11.2026): ящики смотрят там же, где домены.
 * Ящиков нет — блока нет: об этом уже сказала строка экрана.
 *
 * **Свёрнут, итог — в подписи** (разведка 10.11.2026): ящики по одному на домен повторяли строки
 * доменов над ними, и страница уходила за три тысячи пикселей. Подпись говорит, что внутри и сколько:
 * «4 ящика: 3 на разгоне, 1 — до 20 в день», и сколько сегодня не пишут — это то, по чему решают,
 * раскрывать ли.
 *
 * **Отказ прогноза — на виду, в этом блоке**, домены выше живут: ручки разные. «Не обновилось» —
 * над свёрнутым блоком, а не внутри: предупреждение не прячется.
 */

import { Box, Card, Stack } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { fetchStageBoxes } from '../api/senders';
import type { StageBox } from '../api/senders';
import type { Stage } from '../api/stages';
import { FoldCard } from '../components/FoldCard';
import { NoAnswer, NotRefreshed } from '../components/MailingBlock';
import { formatNumber, plural } from '../format';
import { MailingBoxes } from './MailingBoxes';

/** Перечитывается раз в минуту, как прогноз рассылки на вкладках этапов. */
const BOXES_REFRESH_MS = 60_000;
const NB = '\u00a0';

function count(n: number, one: string, few: string, many: string): string {
  return `${formatNumber(n)}${NB}${plural(n, one, few, many)}`;
}

/** Доля ящиков: сколько и какие — на разгоне, с законченным разгоном (до капа в день), выключены. */
interface Share {
  n: number;
  words: string;
}

function shares(boxes: StageBox[]): Share[] {
  const on = boxes.filter((box) => box.enabled);
  const warming = on.filter((box) => box.warmup_day > 0 && !box.warmup_finished).length;
  const caps = [...new Set(on.map((box) => box.daily_cap))];
  const cap = caps.length === 1 ? `до${NB}${formatNumber(caps[0])} в${NB}день` : 'разгон окончен';
  const off = boxes.length - on.length;
  const all: Share[] = [
    { n: warming, words: 'на разгоне' },
    { n: on.length - warming, words: `— ${cap}` },
    { n: off, words: plural(off, 'выключен', 'выключены', 'выключены') },
  ];
  return all.filter((share) => share.n > 0);
}

/** Итог ящиков одной строкой — подпись свёрнутого блока: «4 ящика: 3 на разгоне, 1 — до 20 в день».
 *  Одна доля — без перечисления: «4 ящика — до 20 в день», а не «4 ящика: 4 — …». */
export function boxesSummary(boxes: StageBox[]): string {
  const total = count(boxes.length, 'ящик', 'ящика', 'ящиков');
  const found = shares(boxes);
  const head =
    found.length === 1
      ? `${total} ${found[0]?.words ?? ''}`
      : `${total}: ${found.map((share) => `${formatNumber(share.n)}${NB}${share.words}`).join(', ')}`;
  const silent = boxes.filter((box) => box.enabled && box.refused !== null).length;
  return silent === 0
    ? head
    : `${head} · ${count(silent, 'сегодня не пишет', 'сегодня не пишут', 'сегодня не пишут')}`;
}

export function StageBoxes({ stage }: { stage: Stage }) {
  const [open, setOpen] = useState(false);
  const forecast = useQuery({
    queryKey: ['senders', 'boxes', stage],
    queryFn: () => fetchStageBoxes(stage),
    refetchInterval: BOXES_REFRESH_MS,
  });
  const view = forecast.data;
  if (view !== undefined && view.senders.length === 0) return null;
  if (view === undefined) {
    return (
      <Card className="glassPanel" p="md">
        <NoAnswer query={forecast} failed="Ящики не загрузились" loading="Загружаем ящики" />
      </Card>
    );
  }
  return (
    <Stack gap="xs" className="boxesFold">
      <NotRefreshed query={forecast} />
      <FoldCard
        label={`Ящики и разгон · ${boxesSummary(view.senders)}`}
        open={open}
        onToggle={() => setOpen((was) => !was)}
      >
        {/* Таблица — на поле своих ячеек: текст встаёт на кромку соседних карточек (32 px от края
            стекла), а не на 16 px правее; на телефоне строки — карточками без полей ячеек. */}
        <Box mt="sm" mx={{ base: 0, sm: 'calc(var(--mantine-spacing-md) * -1)' }}>
          <MailingBoxes view={view} />
        </Box>
      </FoldCard>
    </Stack>
  );
}
