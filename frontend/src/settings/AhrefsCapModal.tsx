/**
 * «Изменить кап» на экране «Расход»: весь лимит ключа Ahrefs — или не больше N за цикл ключа.
 *
 * Решения владельца 16.10.2026: кап меняют здесь, а не правкой окружения на сервере, и видно,
 * кто, когда и что поставил; сам кап — от ключа. «Весь лимит ключа» — по умолчанию: кап следует
 * за ключом, когда лимит двигают в Ahrefs. «Не больше N» сужает его. Смета прогона с «не
 * помещается» ведёт сюда же.
 *
 * **Границы — сервера** (`limits`): от единицы до размера ключа из последнего ответа Ahrefs.
 * Поле проверяется ими до нажатия — за границей под полем словами, почему нельзя, и
 * «Сохранить» заперта; своей копии чисел у экрана нет. Размер ключа неизвестен (Ahrefs ещё
 * не ответил) — «не больше N» задать не от чего, и сказано это под полем; «весь лимит ключа»
 * можно и тогда.
 *
 * **Отказ сервера — над полем, вписанное остаётся** (правило форм, проверка QA 10.10.2026):
 * уведомление в углу закрывало бы кнопки окна.
 *
 * **Окно открывается чистым** — на действующем выборе: экран монтирует его заново на каждое
 * открытие (`AhrefsCap`), как «Больше не писать» на «Стоп-листе».
 */

import { Radio, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { saveAhrefsCap } from '../api/settings';
import type { AhrefsKey, CapChange } from '../api/settings';
import type { ThresholdRange } from '../api/types';
import { NumberField } from '../components/NumberField';
import { numberRefusal, numberText, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatNumber } from '../format';
import { CapDialog, RecentChanges, useCapSave } from './CapDialog';

/** Поле — по числу «1 000 000», а не на ширину окна. */
const INPUT_WIDTH = '9rem';

/** Выбор окна: весь лимит ключа или «не больше N». */
type Kind = 'whole' | 'limit';

/** Что поставили — словами: число или «весь лимит ключа». */
export function capWords(unitsCap: number | null): string {
  return unitsCap === null ? 'весь лимит ключа' : `не больше ${formatNumber(unitsCap)}`;
}

function ruleOf(limits: ThresholdRange): NumberRule {
  return { decimals: 0, min: limits.min, max: limits.max };
}

/** Размера ключа нет — «не больше N» задать не от чего: строкой вместо поля. */
const NO_LIMITS =
  'Размер ключа неизвестен — Ahrefs ещё не ответил, «не больше N» задать не от чего';

/** Что не так с вписанным — словами; `null` — годится. Пусто — «впишите число», а не ноль:
 *  ноль — тоже кап, и его никто не набирал. */
export function capRefusal(text: string, limits: ThresholdRange): string | null {
  if (text.trim() === '') return 'Впишите число';
  return numberRefusal(text, ruleOf(limits));
}

/** Что выбрано в окне: вид капа, вписанное число, что уйдёт на сервер и можно ли сохранить. */
interface Choice {
  kind: Kind;
  setKind: (kind: Kind) => void;
  text: string;
  setText: (text: string) => void;
  /** Что уйдёт на сервер: число или `null` — весь лимит ключа. */
  wanted: number | null;
  /** Выбор годен: «весь лимит ключа» или число в границах ключа. */
  ready: boolean;
  /** То же, что действует, — менять нечего. */
  same: boolean;
}

/** Выбор окна — с действующего: «не больше N» открывается с N в поле. */
function useChoice(key: AhrefsKey): Choice {
  const [kind, setKind] = useState<Kind>(key.chosen === null ? 'whole' : 'limit');
  const [text, setText] = useState(() => (key.chosen === null ? '' : numberText(key.chosen)));
  const typed = key.limits === null ? null : validNumber(text, ruleOf(key.limits));
  const wanted = kind === 'whole' ? null : typed;
  const ready = kind === 'whole' || typed !== null;
  return { kind, setKind, text, setText, wanted, ready, same: ready && wanted === key.chosen };
}

/** Уведомление после смены — словами того, что поставили. */
function savedWords(change: CapChange): string {
  const unit = change.units_cap === null ? '' : ' юнитов за цикл ключа';
  return `Кап — ${capWords(change.units_cap)}${unit}. Сметы и потолки прогонов считают от него с этой минуты.`;
}

interface ChoiceProps {
  ahrefsKey: AhrefsKey;
  choice: Choice;
  /** Выбор изменился — прежний отказ сервера больше не про него. */
  onEdit: () => void;
}

/** «Весь лимит ключа» или «не больше N»: размер ключа — подписью, поле — в границах ключа;
 *  ключ неизвестен — «не больше» заперт, и строкой вместо поля сказано почему. */
function CapChoice({ ahrefsKey: key, choice, onEdit }: ChoiceProps) {
  const follows = 'кап следует за ключом, когда лимит двигают в Ahrefs';
  return (
    <>
      <Radio.Group
        label="Кап"
        value={choice.kind}
        onChange={(next) => {
          choice.setKind(next === 'limit' ? 'limit' : 'whole');
          onEdit();
        }}
      >
        <Stack gap={6} mt={6}>
          <Radio
            value="whole"
            label="Весь лимит ключа"
            description={
              key.size === null
                ? 'Кап следует за ключом, когда лимит двигают в Ahrefs'
                : `Сейчас ${formatNumber(key.size)} юнитов за цикл — ${follows}`
            }
          />
          <Radio value="limit" label="Не больше" disabled={key.limits === null} />
        </Stack>
      </Radio.Group>
      {key.limits === null ? (
        <Text size="sm" c="dimmed">
          {NO_LIMITS}
        </Text>
      ) : choice.kind === 'limit' ? (
        <NumberField
          label="Юнитов за цикл ключа"
          description={`От ${formatNumber(key.limits.min)} до ${formatNumber(key.limits.max)} — лимита ключа`}
          styles={{ wrapper: { width: INPUT_WIDTH } }}
          grouped
          value={choice.text}
          error={capRefusal(choice.text, key.limits)}
          data-autofocus
          onChange={(next) => {
            choice.setText(next);
            onEdit();
          }}
        />
      ) : null}
    </>
  );
}

interface Props {
  ahrefsKey: AhrefsKey;
  onClose: () => void;
}

export function AhrefsCapModal({ ahrefsKey: key, onClose }: Props) {
  const choice = useChoice(key);
  // Кап читают «Расход» и «Обзор»; смету прогона человек считает заново сам.
  const save = useCapSave({
    save: saveAhrefsCap,
    words: savedWords,
    onClose,
    readers: ['overview'],
  });

  return (
    <CapDialog
      title="Кап Ahrefs"
      intro={
        <Text size="sm" c="dimmed">
          Кап держит наш расход юнитов Ahrefs за цикл ключа: прогон, который не помещается в остаток
          капа, не запускается, и обход Этапа 2 не покупает DR сверх него. Новый кап действует
          сразу.
        </Text>
      }
      onClose={onClose}
      onSave={() => save.mutate(choice.wanted)}
      error={save.error}
      errorTitle="Кап не сменили"
      saving={save.isPending}
      disabled={!choice.ready}
      locked={choice.same ? 'Это действующий кап — выберите другой' : null}
    >
      <CapChoice ahrefsKey={key} choice={choice} onEdit={() => save.reset()} />
      {key.changes.length > 0 && (
        <RecentChanges
          changes={key.changes.map((change) => ({ ...change, what: capWords(change.units_cap) }))}
        />
      )}
    </CapDialog>
  );
}
