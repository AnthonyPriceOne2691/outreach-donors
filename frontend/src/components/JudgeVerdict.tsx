/**
 * Вердикт судьи и ответ донора — одним видом на всех экранах.
 *
 * **Вердикт есть у каждого домена, и его отсутствие — тоже ярлык.**
 * «Судья не смотрел» — не пустое место, а пропуск: если таких много,
 * сломан судья, а не сайты. Тип сайта, совет и слой стоят рядом, чтобы
 * по расхождению с решением человека было видно, какой тип и какой слой
 * судья путает — и чинить систематически, а не по одному домену.
 *
 * **Цитата обязательна к показу.** Без неё вердикт нельзя проверить,
 * а проверка — то, ради чего человек смотрит очередь.
 *
 * **Значки переносятся, а не режутся.** Ряд «не площадка · продаёт
 * размещение · арбитр» шире колонки судьи, и в одну строку он ужимал
 * значки до «площа…» и «арб…» (аудит 25.09.2026): слово, обрезанное до
 * четырёх букв, не значит ничего. Признаки главной — словами, а не
 * метками сервера («cart:/warenkorb» → «ссылка на корзину»).
 */

import { Anchor, Badge, Group, Stack, Text, Tooltip } from '@mantine/core';

import { homeSignalsText, JUDGE_ADVICE, JUDGE_DECIDERS, SITE_INTENTS } from '../api/labels';
import type { MachineView, SellerView } from '../api/types';

export function JudgeVerdict({ machine }: { machine: MachineView }) {
  if (machine.recommendation === null) {
    return (
      <Badge variant="outline" color="yellow">
        судья не смотрел
      </Badge>
    );
  }
  const advice = JUDGE_ADVICE[machine.recommendation];
  const who = machine.decided_by === null ? null : JUDGE_DECIDERS[machine.decided_by];
  const intent = machine.intent === null ? null : (SITE_INTENTS[machine.intent] ?? 'другой тип');
  return (
    <Stack gap={4} align="center">
      <Group gap={6} justify="center" wrap="wrap">
        <Badge variant="light" color={advice.color}>
          {advice.title}
        </Badge>
        {intent !== null && (
          <Badge variant="outline" color="gray" size="sm">
            {intent}
          </Badge>
        )}
        {who !== null && (
          <Tooltip label={who.hint} withArrow>
            <Badge variant="outline" color="gray" size="sm">
              {who.title}
            </Badge>
          </Tooltip>
        )}
      </Group>
      {machine.quote !== null ? (
        <Text size="xs" fs="italic" ta="center" maw={260}>
          «{machine.quote}»
        </Text>
      ) : (
        <Text size="xs" c="dimmed" ta="center" maw={260}>
          {machine.reason}
        </Text>
      )}
      <Group gap={8} justify="center">
        {machine.source_url !== null && (
          <Anchor href={machine.source_url} target="_blank" rel="noreferrer" size="xs">
            страница
          </Anchor>
        )}
        {machine.home_shop.length > 0 && (
          <Text size="xs" c="dimmed" ta="center">
            главная: {homeSignalsText(machine.home_shop)}
          </Text>
        )}
        {machine.home_reached === false && (
          <Text size="xs" c="dimmed">
            главная не открылась
          </Text>
        )}
      </Group>
    </Stack>
  );
}

/** Ответ самого донора на письмо. Для гест-постинга — правда первого
 *  сорта: сильнее судьи и человека, и экран показывает его отдельно. */
export function SellerAnswer({ seller }: { seller: SellerView }) {
  if (seller.answer === null) {
    return (
      <Text size="xs" c="dimmed">
        не отвечал
      </Text>
    );
  }
  if (seller.answer === 'free') {
    return (
      <Badge variant="light" color="green">
        берёт бесплатно
      </Badge>
    );
  }
  if (seller.answer === 'declines') {
    return (
      <Badge variant="light" color="red">
        не продаёт
      </Badge>
    );
  }
  return (
    <Stack gap={2} align="center">
      <Badge variant="light" color="green">
        продаёт
      </Badge>
      {seller.price !== null && (
        <Text size="xs" c="dimmed">
          {seller.price} {seller.currency ?? ''}
        </Text>
      )}
    </Stack>
  );
}
