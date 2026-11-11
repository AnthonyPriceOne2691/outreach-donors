/**
 * «Ждут проверки адреса: N · не проверить: K · адрес не существует: M» на экране писем —
 * рядом с «ждут адреса» (11.11.2026). Адрес проверяется перед первым письмом, при сборке
 * очереди (`addressCheck.ts`); строка говорит, скольким письмо соберётся только после проверки,
 * у скольких проверить нельзя и кого проверка сняла. Что это значит — в «i». Сказать нечего
 * или проверка этапа выключена — строки нет.
 */

import { Group, Text } from '@mantine/core';

import { InfoHint } from '../components/InfoHint';
import { formatNumber } from '../format';
import { checkCounts, checkHint, checkTail, checkWorthSaying } from './addressCheck';

export function CheckLine({ funnel }: { funnel: Record<string, number> }) {
  const counts = checkCounts(funnel);
  if (!checkWorthSaying(counts)) return null;
  const tail = checkTail(counts);
  return (
    <Group gap={6} align="center" className="addressCheckLine">
      {counts.waiting > 0 ? (
        <Text size="sm">
          ждут проверки адреса:{'\u00a0'}
          <b>{formatNumber(counts.waiting)}</b>
        </Text>
      ) : null}
      {tail.length > 0 ? (
        <Text size="sm" c="dimmed">
          {counts.waiting > 0 ? '· ' : ''}
          {tail.join(' · ')}
        </Text>
      ) : null}
      <InfoHint name="Проверка адреса перед письмом" width={360} interactive>
        {checkHint(counts)}
      </InfoHint>
    </Group>
  );
}
