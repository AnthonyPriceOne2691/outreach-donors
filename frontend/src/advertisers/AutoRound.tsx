/**
 * Автокруг рекламодателей в блоке «К письму»: выключатель, суточный потолок писем и итог круга.
 *
 * Слово владельца 17.11.2026: три кнопки после ночи — «Перевести в рекламодатели», «Собрать
 * очередь» и «Отправить очередь» с «дальше — сама» — сервис жмёт сам, раз в сутки после ночного
 * шага судьи (`crawl/auto_round.py`), если выключатель включён.
 *
 * **Почему здесь, а не у строк судьи.** Круг делает то же, что кнопки этого блока и экрана писем:
 * переводит, ищет адреса, собирает и отпускает очередь. Строки судьи — о решениях по спорным, а
 * выключатель рядом с «Перевести» отвечает на вопрос «кто нажмёт эту кнопку завтра». К блоку и так
 * ведёт «К письму ждут перевода» от строк судьи.
 *
 * **Включение — с подтверждением у кнопки** (`ConfirmPopover`): с этой минуты письма уходят без
 * человека, и вопрос называет, что круг делает, сколько писем и когда пойдёт первый. **Выключение —
 * одним нажатием**, как у полос судьи. Потолок писем — числом рядом, границами сервера; у
 * включённого — «Сохранить». Без прав на перевод и отправку разом — только строки, без кнопок.
 *
 * **Итог — одной строкой** (`docs/UI_RULES.md`): «Автокруг 17.11, 08:12: переведено 24 · …»;
 * подробности — в «i» строками сервера. Круг идёт или вот-вот начнётся — строка перечитывается
 * сама; кончился — перечитываются числа «К письму» и строки судьи.
 */

import { Alert, Button, Card, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import type { AutoRound as Round, AutoRoundLast } from '../api/advertisersAutoRound';
import { AUTO_ROUND_KEY, fetchAutoRound, setAutoRound } from '../api/advertisersAutoRound';
import { JUDGE_NIGHT_KEY } from '../api/advertisersJudgeNight';
import { refusalOf } from '../api/client';
import { useSession } from '../auth/AuthProvider';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { HintLines } from '../components/HintLines';
import { InfoHint } from '../components/InfoHint';
import { NumberField } from '../components/NumberField';
import { numberRefusal, numberText, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatDate, formatNumber, formatTime } from '../format';
import { ADVERTISER_CONTACTS, PROMOTION_QUERY_KEY } from './contactsSource';
import { dayMonth } from './JudgeNight';

/** Пока круг идёт или вот-вот начнётся, строка перечитывается сама. */
const POLL_MS = 30_000;

/** Как устроен автокруг — в «i»: что, когда, сколько стоит и как выключить. */
export const AUTO_ROUND_HINT =
  'Автокруг делает сам то, что после ночи делают три кнопки: «Перевести в рекламодатели», ' +
  '«Собрать очередь» и «Отправить очередь» с отметкой «дальше — сама». Раз в сутки, утром, после ' +
  'ночного шага судьи. Писем — не больше потолка в сутки; уходят они по одному с ящика, в рабочие ' +
  'часы получателя, пачкой — никогда. Деньги — как у кнопок: платный поиск адреса, проверка адреса ' +
  'перед письмом и модель на каждое письмо, под потолками «Расхода». «Не понять», ответы и лиды ' +
  'остаются вам. Выключить — одной кнопкой: начатое доходит само, новых задач круг не ставит.';

/** Строку стоит перечитывать: круг идёт — или включён, а сегодняшнего ещё не было. */
export function watched(round: Round | undefined): boolean {
  if (round === undefined) return false;
  if (round.last !== null && round.last.going) return true;
  return round.switch.enabled && !round.ran_today;
}

/** «Автокруг 17.11, 08:12: …» — дата круга сервера, время по часам смотрящего. */
export function roundLine(last: AutoRoundLast): string {
  const at = last.at === null ? '' : `, ${formatTime(last.at)}`;
  return `Автокруг ${dayMonth(last.day)}${at}: ${last.words}`;
}

/** Как стоит выключатель — словами. */
export function switchWords(round: Round): string {
  const { enabled, letters, set_by: by, set_at: at } = round.switch;
  if (!enabled) return 'Выключен: перевод, сборка и отправка — кнопками.';
  const who = at === null ? '' : ` — включил${by === null ? '' : ` ${by}`} ${formatDate(at)}`;
  return `Каждый день сам, до ${formatNumber(letters)} писем в сутки${who}.`;
}

/** Что включение значит — до нажатия: что круг сделает, сколько писем и когда первый. */
export function enableWarning(round: Round, letters: number): string {
  return (
    'Каждый день после ночного шага судьи сервис сам переведёт «куплено» и «пишем» в ' +
    `рекламодатели, найдёт им адреса, соберёт до ${formatNumber(letters)} писем и отпустит очередь ` +
    `«По найденной ссылке» уходить саму — от вашего имени. ${round.first}`
  );
}

/** Кончился круг — числа «К письму» и строки судьи уже другие. */
function useRoundEnd(round: Round | undefined) {
  const client = useQueryClient();
  const was = useRef(false);
  const going = round?.last?.going ?? false;
  useEffect(() => {
    if (was.current && !going) {
      for (const key of [PROMOTION_QUERY_KEY, ADVERTISER_CONTACTS.queryKey, JUDGE_NIGHT_KEY]) {
        void client.invalidateQueries({ queryKey: key });
      }
    }
    was.current = going;
  }, [going, client]);
}

/** Итог последнего круга строкой; подробности — в «i» строками сервера. */
function LastRound({ last }: { last: AutoRoundLast }) {
  const trouble = last.status === 'failed' || last.status === 'missed';
  return (
    <Group gap={4} align="center" wrap="nowrap">
      <Text size="sm" c={trouble ? 'inherit' : 'dimmed'}>
        {roundLine(last)}
      </Text>
      {last.lines.length > 0 ? (
        <InfoHint name="Итог круга целиком" width={340} interactive>
          <HintLines lines={last.lines} />
        </InfoHint>
      ) : null}
    </Group>
  );
}

/** Потолок писем и выключатель: включить — после вопроса, выключить — сразу. */
function Controls({ round }: { round: Round }) {
  const client = useQueryClient();
  const saved = round.switch.letters;
  const [text, setText] = useState(numberText(saved));
  const rule: NumberRule = { decimals: 0, min: round.letters_min, max: round.letters_max };
  const letters = validNumber(text, rule);
  const change = useMutation({
    mutationFn: ({ on, cap }: { on: boolean; cap: number }) => setAutoRound(on, cap),
    onSuccess: (view) => {
      client.setQueryData(AUTO_ROUND_KEY, view);
      setText(numberText(view.switch.letters));
    },
  });
  const on = round.switch.enabled;
  return (
    <Stack gap={6}>
      {change.error !== null && (
        <Alert color="red" title="Автокруг не переключён">
          {refusalOf(change.error)}
        </Alert>
      )}
      <Group gap="sm" align="flex-end">
        <NumberField
          label="Писем в сутки"
          w="9rem"
          refusalAbove
          value={text}
          error={numberRefusal(text, rule)}
          onChange={setText}
        />
        {on && letters !== null && letters !== saved ? (
          <Button
            variant="default"
            className="press"
            loading={change.isPending}
            onClick={() => change.mutate({ on: true, cap: letters })}
          >
            Сохранить
          </Button>
        ) : null}
        {on ? (
          <Button
            variant="default"
            className="press"
            loading={change.isPending}
            onClick={() => change.mutate({ on: false, cap: saved })}
          >
            Выключить
          </Button>
        ) : (
          <ConfirmPopover
            message={enableWarning(round, letters ?? saved)}
            confirm="Включить"
            onConfirm={() => letters !== null && change.mutate({ on: true, cap: letters })}
          >
            {(ask) => (
              <Button
                variant="light"
                className="press"
                loading={change.isPending}
                disabled={letters === null}
                onClick={ask}
              >
                Включить автокруг…
              </Button>
            )}
          </ConfirmPopover>
        )}
      </Group>
    </Stack>
  );
}

export function AutoRound() {
  const { can } = useSession();
  const { data, error } = useQuery({
    queryKey: AUTO_ROUND_KEY,
    queryFn: fetchAutoRound,
    refetchInterval: (query) => (watched(query.state.data) ? POLL_MS : false),
  });
  useRoundEnd(data);
  if (data === undefined) {
    // Пропавший молча выключатель читался бы как «автокруга нет», а не «не загрузилось».
    return error ? (
      <Text size="sm" c="dimmed">
        Автокруг не загрузился: {refusalOf(error)}
      </Text>
    ) : null;
  }
  if (!data.available) {
    return (
      <Text size="sm" c="dimmed">
        Автокруг выключен настройкой сервиса: перевод, сборка и отправка — кнопками.
      </Text>
    );
  }
  return (
    <Card className="glassQuiet" p="sm">
      <Stack gap={6} role="group" aria-label="Автокруг рекламодателей">
        <Group gap="xs">
          <Text size="sm" fw={500}>
            Автокруг
          </Text>
          <InfoHint name="Как устроен автокруг" width={360}>
            {AUTO_ROUND_HINT}
          </InfoHint>
        </Group>
        <Text size="sm" c={data.switch.enabled ? 'inherit' : 'dimmed'}>
          {switchWords(data)}
        </Text>
        {can('run') && can('send') ? <Controls round={data} /> : null}
        {data.last !== null ? <LastRound last={data.last} /> : null}
      </Stack>
    </Card>
  );
}
