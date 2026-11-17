/**
 * Настройки обхода 24/7 в его блоке (`CrawlAlways`, 17.11.2026): порог «мало рекламодателей» и
 * переобход слабых сайтов. Без права запуска — те же настройки строками.
 *
 * **Порог — фразой с полем**: «Мало рекламодателей: меньше [0,1] % страниц с платной ссылкой на 10
 * 000». Строкой под ней — сколько сайтов получат пометку при набранном пороге: экран считает по
 * долям сервера (`yields`), пока набирают, без запроса. Граница 0–100, шаг 0,01, запятая — как
 * точка; за границей и пустое поле — отказ на месте этой строки, кромка поля — розой, «Сохранить»
 * закрыт. Строка под фразой — пояснение самого поля (`description`), а не соседний текст: читалка
 * называет её вместе с полем.
 *
 * **«Сохранить» — только когда набрано другое**, как у потолка автокруга (`AutoRound`). Поле,
 * которого не трогали, идёт за сервером: порог мог сменить коллега, пока экран открыт.
 *
 * **«Переобход даже слабых сайтов» — переключателем в одно нажатие** в обе стороны.
 */

import { Box, Button, Group, Switch, Text } from '@mantine/core';
import type { UseMutationResult } from '@tanstack/react-query';
import { useState } from 'react';
import type { ReactNode } from 'react';

import type { AlwaysChange, AlwaysView } from '../api/crawlAlways';
import { NumberField } from '../components/NumberField';
import { numberOf, numberRefusal, numberText, sameNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatNumber } from '../format';
import { markWords, PART_PAGES, percentText } from './crawlAlwaysWords';

/** Смена настроек 24/7 — одна на блок: кнопки, поле и переключатель ждут друг друга. */
export type AlwaysChanger = UseMutationResult<AlwaysView, Error, AlwaysChange>;

/** Порог — процентами, от 0 до 100, с сотыми. */
const RULE: NumberRule = { decimals: 2, min: 0, max: 100 };

/** Имя поля порога для читалки: видимая подпись — фраза вокруг поля. */
export const THRESHOLD_NAME = 'Порог «мало рекламодателей», % страниц с платной ссылкой';

export const RECRAWL_LABEL = 'Переобход даже слабых сайтов';

/** Что набрано в поле порога: годится ли и что сказать строкой под ним. Стёртое поле — «впишите
 *  число», а не ноль: ноль — тоже порог, и его никто не набирал. */
function typedThreshold(text: string) {
  const refusal = text.trim() === '' ? 'Впишите число' : numberRefusal(text, RULE);
  return { refusal, value: refusal === null ? numberOf(text) : null };
}

/** Фраза вокруг поля порога и «Сохранить» — одним рядом, переносом на телефоне. */
function Sentence({ field, save }: { field: ReactNode; save: ReactNode }) {
  return (
    <Group gap={6} align="center">
      <Text size="sm">Мало рекламодателей: меньше</Text>
      <Box w="5rem">{field}</Box>
      <Text size="sm">% страниц с платной ссылкой на {formatNumber(PART_PAGES)}</Text>
      {save}
    </Group>
  );
}

/** Поле порога во фразе; под ней — сколько сайтов получат пометку или почему порог не годится
 *  (отказ встаёт на место пояснения розой — `refusalAbove`). */
function Threshold({ view, change }: { view: AlwaysView; change: AlwaysChanger }) {
  const saved = numberText(view.threshold_pct, 2);
  const [typed, setTyped] = useState<string | null>(null);
  const text = typed ?? saved;
  const { refusal, value } = typedThreshold(text);
  const busy = change.isPending;
  const save = () => {
    if (value === null) return;
    // Сохранено — поле снова идёт за сервером.
    change.mutate({ threshold_pct: value }, { onSuccess: () => setTyped(null) });
  };
  const button = sameNumber(text, saved) ? null : (
    <Button
      variant="default"
      className="press"
      loading={busy && change.variables?.threshold_pct !== undefined}
      disabled={value === null || busy}
      onClick={save}
    >
      Сохранить
    </Button>
  );
  return (
    <NumberField
      decimals={2}
      refusalAbove
      aria-label={THRESHOLD_NAME}
      value={text}
      error={refusal}
      description={markWords(view.yields, value ?? view.threshold_pct)}
      inputWrapperOrder={['input', 'description']}
      inputContainer={(field) => <Sentence field={field} save={button} />}
      onChange={setTyped}
    />
  );
}

/** Переключатель: пока сервер не ответил, стоит так, как нажали. */
function Recrawl({ view, change }: { view: AlwaysView; change: AlwaysChanger }) {
  const asked = change.isPending ? change.variables?.recrawl_weak : undefined;
  return (
    <Switch
      size="sm"
      label={RECRAWL_LABEL}
      checked={asked ?? view.recrawl_weak}
      disabled={change.isPending}
      onChange={(event) => change.mutate({ recrawl_weak: event.currentTarget.checked })}
    />
  );
}

/** Без права запуска — те же настройки строками. */
function SettingsWords({ view }: { view: AlwaysView }) {
  const threshold = percentText(view.threshold_pct);
  const marked = markWords(view.yields, view.threshold_pct);
  return (
    <>
      <Text size="sm" c="dimmed">
        Мало рекламодателей: меньше {threshold} страниц с платной ссылкой на{' '}
        {formatNumber(PART_PAGES)} — {marked}.
      </Text>
      <Text size="sm" c="dimmed">
        {RECRAWL_LABEL}: {view.recrawl_weak ? 'включён' : 'выключен'}.
      </Text>
    </>
  );
}

export function AlwaysSettings({
  view,
  change,
  mayRun,
}: {
  view: AlwaysView;
  change: AlwaysChanger;
  mayRun: boolean;
}) {
  if (!mayRun) return <SettingsWords view={view} />;
  return (
    <>
      <Threshold view={view} change={change} />
      <Recrawl view={view} change={change} />
    </>
  );
}
