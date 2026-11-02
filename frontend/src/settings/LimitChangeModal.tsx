/**
 * «Изменить» на панели лимитов: новое значение, «Просчитать» — и только потом «Подтвердить».
 *
 * Требование другой стороны: то, что можно поменять, меняется с кнопкой просчёта — сколько будет
 * стоить новый лимит, перед его подтверждением. Просчёт — вопрос серверу, он ничего не пишет.
 *
 * **«Подтвердить» — только после просчёта именно этого значения.** Смена числа в поле сбрасывает
 * просчёт: стоимость старого значения рядом с новым читалась бы ответом на него. Ответ, пришедший
 * после правки поля, тоже не считается: просчёт сверяется с тем, что сейчас в поле.
 *
 * **Границы — сервера**, и у каждой строки свои: поле проверяет только, что это целое число, а что
 * допустимо, говорит просчёт. Отказ просчёта остаётся словами в окне, вписанное не трогается.
 *
 * **Смена повторяет просчёт на сервере**: с устаревшим значением она откажет сама, и отказ встанет
 * над полем (`CapDialog`), а окно останется открытым с тем, что вписано.
 */

import { Alert, Button, Stack, Text } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import type { KeyboardEvent } from 'react';

import { refusalOf } from '../api/client';
import { changeLimit, quoteLimit } from '../api/limitPanel';
import type { LimitField, LimitQuote, LimitRow } from '../api/limitPanel';
import { NumberField } from '../components/NumberField';
import { numberRefusal, numberText, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatNumber, formatUsd } from '../format';
import { CapDialog, useCapSave } from './CapDialog';

/** Поле — по числу «200 000 000», а не на ширину окна. */
const INPUT_WIDTH = '11rem';

/** Целое, не меньше нуля: что допустимо, говорит сервер. */
const RULE: NumberRule = { decimals: 0, min: 0 };

/** За что лимит — словами подписи поля. */
const PERIOD: Record<LimitField, string> = {
  per_session: 'за сессию',
  per_day: 'в день',
  per_month: 'в месяц',
};

/** Что ответил просчёт: слова, стоимость нового лимита или отказ. */
function Answer({ quote }: { quote: LimitQuote }) {
  if (quote.refusal !== null) {
    return (
      <Alert color="yellow" title="Так нельзя">
        {quote.refusal}
      </Alert>
    );
  }
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        Стоимость нового лимита:{' '}
        {quote.cost_usd === null ? 'деньгами не считается' : formatUsd(quote.cost_usd)}
      </Text>
      <Text size="sm">{quote.words}</Text>
    </Stack>
  );
}

/** Просчёт не дошёл: отказ сервера словами, вписанное остаётся. */
function QuoteFailed({ error }: { error: Error | null }) {
  if (error === null) return null;
  return (
    <Alert color="red" title="Просчёт не вышел">
      {refusalOf(error)}
    </Alert>
  );
}

/** Что не так с вписанным: пусто — «впишите число», а не ноль: ноль никто не набирал. */
function fieldRefusal(text: string): string | null {
  return text.trim() === '' ? 'Впишите число' : numberRefusal(text, RULE);
}

/** Просчёт вписанного: годен только ответ на то, что в поле сейчас. Поздний ответ на прежнее
 *  число — не ответ. */
function useQuote(row: LimitRow, field: LimitField, wanted: number | null) {
  const quote = useMutation({ mutationFn: quoteLimit });
  const fresh = quote.variables?.value === wanted ? (quote.data ?? null) : null;
  const run = () => {
    if (wanted !== null) quote.mutate({ key: row.key, field, value: wanted });
  };
  return { quote, fresh, run };
}

interface Props {
  row: LimitRow;
  field: LimitField;
  onClose: () => void;
}

export function LimitChangeModal({ row, field, onClose }: Props) {
  const current = row[field];
  const [text, setText] = useState(() => (current === null ? '' : numberText(current)));
  const wanted = validNumber(text, RULE);
  const { quote, fresh, run } = useQuote(row, field, wanted);
  const save = useCapSave({
    save: changeLimit,
    words: (done: LimitQuote) => `${row.action}: ${formatNumber(done.accepted)} ${row.unit}`,
    onClose,
    readers: ['limit-panel'],
  });
  const confirm = () => {
    if (wanted !== null && fresh !== null) {
      save.mutate({ key: row.key, field, value: wanted, quoted: fresh.accepted });
    }
  };
  const askOnEnter = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    // Подтверждает только кнопка: два Enter подряд не должны менять лимит, не дав прочитать ответ.
    event.preventDefault();
    if (fresh === null && !quote.isPending) run();
  };

  return (
    <CapDialog
      title={row.service}
      intro={
        <Text size="sm" c="dimmed">
          {row.action}. Охват: {row.scope}. Новое значение действует с этой минуты. Стоимость и что
          встанет на деле называет просчёт — он ничего не записывает.
        </Text>
      }
      onClose={onClose}
      onSave={confirm}
      error={save.error}
      errorTitle="Лимит не сменили"
      saving={save.isPending}
      saveLabel="Подтвердить"
      disabled={wanted === null || fresh?.refusal != null}
      locked={fresh === null ? 'Подтвердить можно после просчёта этого значения' : null}
    >
      <NumberField
        label={`Новый лимит, ${row.unit} ${PERIOD[field]}`}
        styles={{ wrapper: { width: INPUT_WIDTH } }}
        grouped
        value={text}
        error={fieldRefusal(text)}
        data-autofocus
        onKeyDown={askOnEnter}
        onChange={(next) => {
          setText(next);
          quote.reset();
          save.reset();
        }}
      />
      <Button
        variant="default"
        className="press"
        style={{ alignSelf: 'flex-start' }}
        disabled={wanted === null}
        loading={quote.isPending}
        onClick={run}
      >
        Просчитать
      </Button>
      <QuoteFailed error={quote.error} />
      {fresh !== null && <Answer quote={fresh} />}
    </CapDialog>
  );
}
