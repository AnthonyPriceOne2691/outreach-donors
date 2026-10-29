import { Alert, Badge, Box, Group, Stack } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { fetchWatchdog } from '../api/settings';
import type { StagedAlarm } from '../api/settings';
import { stageShown } from '../api/stages';
import type { Stage } from '../api/stages';
import { useSession } from '../auth/AuthProvider';

/** Заголовок тревоги — с меткой, есть ли повод для беспокойства. Слово метки — экрана, как у
 *  соседних значков; почему и что делать — строкой сервера под тревогой. Цвет — от смысла:
 *  повод есть — янтарь, «нужно внимание»; повода нет — мята. Тревога без пометки (у продаж
 *  она не обязательна) — без метки. */
function alarmTitle({ title, concern }: StagedAlarm): ReactNode {
  if (!concern) return title;
  return (
    <Group component="span" gap="xs" wrap="wrap">
      <span>{title}</span>
      <Badge component="span" variant="light" color={concern.cause ? 'yellow' : 'green'}>
        {concern.cause ? 'есть повод' : 'повода нет'}
      </Badge>
    </Group>
  );
}

/**
 * Тревоги сторожа тишины — на главной, а не в отдельном разделе: поломка
 * этого класса не показывает себя нигде, и человек не пойдёт её искать.
 *
 * **Когда тихо, сторожа на экране нет.** Карточка «Сторож тишины» с текстом
 * «тихо и правильно» стояла на главной всегда и ничего не сообщала
 * (замечание 25.09.2026: «убрать плашку сторожа с главной»). Сторож
 * по-прежнему спрашивается каждые пять минут, и тревога встаёт красной
 * полосой над сводкой — появление полосы и есть сигнал.
 *
 * Своим файлом с 10.10.2026: «Обзор» упёрся в предел длины файла.
 *
 * **Раздел этапа — тревоги о почте своего этапа** (`stage`): раздел «Продажи» ставит их над
 * вкладками. Ящик переписки выбыл, а её письма ждут именно его (`ops/mail_watch.py`) — человек
 * продаж видит это красной полосой там, где работает, а не только на главной (слово владельца
 * 12.10.2026: вместо смены ящика переписки — красная тревога). Общие тревоги — на главной.
 * Раздел спрашивает сервер своим этапом (`?stage=`): тревоги этапа без опроса провайдеров —
 * тот идёт десятки секунд, а полоса спрашивает при открытии раздела и каждые пять минут.
 *
 * **Стоит ли тревога внимания** (слово Anthony 29.10.2026): метка «есть повод» или «повода нет»
 * у заголовка и строка пометки под подробностями — те же слова, что последней строкой в
 * Telegram (`ops/alarms.Concern`). Поводом стала тревога о доставке, которая встревожила
 * человека, хотя исход письма пришёл бы сам.
 */
export function Watchdog({ stage }: { stage?: Stage }) {
  const { can } = useSession();
  const { data } = useQuery({
    queryKey: ['watchdog', stage ?? null],
    queryFn: () => fetchWatchdog(stage),
    enabled: can('view'),
    // Тревоги меняются часами, а не секундами: спрашивать чаще незачем.
    refetchInterval: 5 * 60 * 1000,
  });

  // Тревога о почте продаж — только с правом «Продажи» (П2б), даже пришедшая раньше,
  // чем право сняли; общая тревога (`stage: null`) — всем.
  const alarms = (data?.alarms ?? []).filter(
    (alarm) =>
      (alarm.stage === null || stageShown(alarm.stage, can('sales'))) &&
      (stage === undefined || alarm.stage === stage),
  );
  if (!can('view') || alarms.length === 0) return null;

  return (
    <Stack gap="sm">
      {alarms.map((alarm) => (
        <Alert key={alarm.code} color="red" title={alarmTitle(alarm)}>
          {alarm.detail}
          {alarm.concern && (
            <Box mt={6} fw={500}>
              {alarm.concern.words}
            </Box>
          )}
        </Alert>
      ))}
    </Stack>
  );
}
