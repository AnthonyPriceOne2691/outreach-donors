import { Alert, Badge, Box, Button, Group, Stack } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useId, useState } from 'react';
import type { ReactNode } from 'react';

import { fetchWatchdog } from '../api/settings';
import type { StagedAlarm } from '../api/settings';
import { stageShown } from '../api/stageRights';
import type { Stage } from '../api/stages';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { Unfold } from '../runs/Unfold';

/** Заголовок тревоги — с меткой, есть ли повод для беспокойства. Слово метки — экрана, как у
 *  соседних значков; почему и что делать — строкой сервера под тревогой. Цвет — от смысла:
 *  повод есть — янтарь, «нужно внимание»; повода нет — мята. Тревога без пометки — без метки. */
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

/** Раскрытие у места: кнопка с шевроном, под ней — то, что она раскрывает. Закрытое не
 *  отрисовано (`Unfold`). Подпись кнопки — чернилами: строка тихих тревог лежит прямо на
 *  полотне, и цвет кнопки на бирюзе давал 3,84 : 1; в плашке тревоги — те же чернила, что её
 *  текст. `flush` — подпись кнопки с той же кромки, что текст над ней: кнопка выдвигается на своё
 *  поле влево, а раскрытое остаётся на кромке текста. */
function Unfolding({
  label,
  flush = false,
  children,
}: {
  label: string;
  flush?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <Stack gap="xs">
      <Group ml={flush ? -8 : 0}>
        <Button
          variant="subtle"
          size="compact-sm"
          className="press"
          color="gray"
          c="var(--mantine-color-text)"
          aria-expanded={open}
          aria-controls={id}
          rightSection={
            <IconChevronDown size={16} style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
          }
          onClick={() => setOpen(!open)}
        >
          {label}
        </Button>
      </Group>
      <Unfold open={open}>
        <Box id={id}>{children}</Box>
      </Unfold>
    </Stack>
  );
}

/**
 * Плашка тревоги: заголовок с меткой, под ним — строка пометки, что делать; подробности —
 * по «Подробнее» (разбор экранов 10.11.2026: тревога капа Ahrefs стояла над «Обзором» абзацем
 * в 62 слова). Тревога без пометки — подробностями на виду: другой строки, что случилось,
 * у неё нет.
 */
function AlarmCard({ alarm, color }: { alarm: StagedAlarm; color: string }) {
  const { concern, detail } = alarm;
  return (
    <Alert color={color} title={alarmTitle(alarm)}>
      {concern ? (
        <Stack gap={6}>
          <Box fw={500}>{concern.words}</Box>
          <Unfolding label="Подробнее" flush>
            {detail}
          </Unfolding>
        </Stack>
      ) : (
        detail
      )}
    </Alert>
  );
}

/** Тревоги, о которых беспокоиться незачем: одной тихой строкой, подробности — по нажатию. */
function QuietAlarms({ alarms }: { alarms: StagedAlarm[] }) {
  return (
    <Unfolding label={`Без повода для беспокойства · ${formatNumber(alarms.length)}`}>
      <Stack gap="sm">
        {alarms.map((alarm) => (
          <AlarmCard key={alarm.code} alarm={alarm} color="gray" />
        ))}
      </Stack>
    </Unfolding>
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
 * у заголовка и строка пометки — те же слова, что последней строкой в Telegram
 * (`ops/alarms.Concern`). Поводом стала тревога о доставке, которая встревожила человека, хотя
 * исход письма пришёл бы сам. С 10.11.2026 строка пометки — сразу под заголовком, подробности —
 * по «Подробнее» (`AlarmCard`).
 *
 * **Красная полоса — только когда есть повод** (слово Anthony 02.11.2026: «чтобы не висели и не
 * отвлекали взгляд»). Тревога считается сервером из состояния на сейчас: решённое уходит с экрана
 * само, при следующем опросе. Но кап, выбранный на 84 %, или письмо, которое сервер получателя
 * тянет трое суток, — не поломка, и красными полосами они стояли неделями, делать с ними нечего.
 * Такие — одной тихой строкой с числом, а слова те же, по нажатию.
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

  // Тревога этапа — только с его правом (П2б), даже пришедшая раньше, чем право сняли;
  // общая тревога (`stage: null`) — всем.
  const alarms = (data?.alarms ?? []).filter(
    (alarm) =>
      (alarm.stage === null || stageShown(alarm.stage, can)) &&
      (stage === undefined || alarm.stage === stage),
  );
  if (!can('view') || alarms.length === 0) return null;
  const quiet = alarms.filter((alarm) => alarm.concern?.cause === false);

  return (
    <Stack gap="sm">
      {alarms
        .filter((alarm) => !quiet.includes(alarm))
        .map((alarm) => (
          <AlarmCard key={alarm.code} alarm={alarm} color="red" />
        ))}
      {quiet.length > 0 ? <QuietAlarms alarms={quiet} /> : null}
    </Stack>
  );
}
