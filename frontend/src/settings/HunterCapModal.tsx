/**
 * «Изменить потолок» проверок адреса на «Расходе» (слово Anthony 02.11.2026: «заведи поле в
 * настройки админа, как с Ahrefs и моделью»).
 *
 * **Пустого значения нет**: снять настройку с экрана нельзя, стёртое поле — «впишите число», а не
 * ноль. Ноль — тоже потолок, его набирают руками: проверки адресов остановлены, и поле говорит это
 * словами. **Границы — сервера** (`month.most`): от нуля до проверок тарифа; своей копии чисел у
 * экрана нет.
 *
 * **Новое значение — со следующего прохода**: проход очистки читает потолок на старте, идущий
 * доходит со старым. Это сказано у поля, иначе смена посреди очистки выглядела бы несработавшей.
 *
 * Потолок ещё из настроек сервера — сохранить то же число можно: оно станет настройкой экрана.
 */

import { Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { saveHunterCap } from '../api/hunter';
import type { HunterCapChange, HunterMonth } from '../api/hunter';
import { NumberField } from '../components/NumberField';
import { numberText, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatNumber } from '../format';
import { capRefusal } from './AhrefsCapModal';
import { CapDialog, RecentChanges, useCapSave } from './CapDialog';

/** Поле — по числу «1 000 000», а не на ширину окна. */
const INPUT_WIDTH = '9rem';

/** Ноль на этом потолке — словами: «0 — проверки адресов остановлены». */
export const STOPPED = '0 — проверки адресов остановлены';

/** Потолок словами: число проверок в месяц или что ноль останавливает. */
export function capWords(cap: number): string {
  return cap === 0 ? STOPPED : `${formatNumber(cap)} проверок в месяц`;
}

/** Уведомление после смены — словами того, что поставили. */
function savedWords(change: HunterCapChange): string {
  const what = capWords(change.verify_cap);
  return `Потолок проверок адреса: ${what}. Новое значение — со следующего прохода очистки.`;
}

interface Props {
  month: HunterMonth;
  onClose: () => void;
}

export function HunterCapModal({ month, onClose }: Props) {
  const [text, setText] = useState(() => (month.cap === null ? '' : numberText(month.cap)));
  const limits = { min: 0, max: month.most };
  const rule: NumberRule = { decimals: 0, ...limits };
  const wanted = validNumber(text, rule);
  const save = useCapSave({ save: saveHunterCap, words: savedWords, onClose });
  // Из настроек сервера то же число — не «то же»: сохранение делает его настройкой экрана.
  const same = month.source === 'setting' && wanted === month.cap;

  return (
    <CapDialog
      title="Потолок проверок адреса"
      intro={
        <Text size="sm" c="dimmed">
          Потолок держит платные проверки адреса за календарный месяц, с первого числа: выбран —
          очистка лидов ждёт следующего месяца. Выбранное считается по журналу расхода.
        </Text>
      }
      onClose={onClose}
      onSave={() => {
        if (wanted !== null) save.mutate(wanted);
      }}
      error={save.error}
      errorTitle="Потолок не сменили"
      saving={save.isPending}
      disabled={wanted === null}
      locked={same ? 'Это действующий потолок — впишите другой' : null}
    >
      <Stack gap={4}>
        <NumberField
          label="Проверок адреса в месяц"
          description={`От ${formatNumber(limits.min)} до ${formatNumber(limits.max)}. ${STOPPED}`}
          styles={{ wrapper: { width: INPUT_WIDTH } }}
          grouped
          value={text}
          error={capRefusal(text, limits)}
          data-autofocus
          onChange={(next) => {
            setText(next);
            save.reset();
          }}
        />
        <Text size="sm" c="dimmed">
          Проход очистки читает потолок на старте: новое значение действует со следующего прохода.
        </Text>
      </Stack>
      {month.changes.length > 0 && (
        <RecentChanges
          changes={month.changes.map((change) => ({
            ...change,
            what: capWords(change.verify_cap),
          }))}
        />
      )}
    </CapDialog>
  );
}
