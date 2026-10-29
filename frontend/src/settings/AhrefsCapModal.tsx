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

import { Alert, Modal, Radio, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { saveAhrefsCap } from '../api/settings';
import type { AhrefsKey, CapChange } from '../api/settings';
import type { ThresholdRange } from '../api/types';
import { FIELD_GAP } from '../components/formRhythm';
import { NumberField } from '../components/NumberField';
import { SaveActions } from '../components/SaveActions';
import { numberRefusal, numberText, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatDateTime, formatNumber } from '../format';
import { notify } from '../notices';

/** Поле — по числу «1 000 000», а не на ширину окна. */
const INPUT_WIDTH = '9rem';

/** Выбор окна: весь лимит ключа или «не больше N». */
type Kind = 'whole' | 'limit';

/** Кто и когда сменил кап: «кап@site.com · 16.10.2026, 14:05»; учётку удалили — только когда.
 *  Тем же словами — смены потолков модели (`LlmCaps.tsx`). */
export function changedBy(change: Pick<CapChange, 'set_by' | 'set_at'>): string {
  const when = formatDateTime(change.set_at);
  return change.set_by === null ? when : `${change.set_by} · ${when}`;
}

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
  const queryClient = useQueryClient();
  const choice = useChoice(key);
  const save = useMutation({
    mutationFn: (unitsCap: number | null) => saveAhrefsCap(unitsCap),
    onSuccess: async (change) => {
      // Кап читают «Расход» и «Обзор»; смету прогона человек считает заново сам.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['usage'] }),
        queryClient.invalidateQueries({ queryKey: ['overview'] }),
      ]);
      notify({ message: savedWords(change), color: 'green' });
      onClose();
    },
  });

  return (
    <Modal opened onClose={onClose} title="Кап Ahrefs">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (choice.ready && !choice.same && !save.isPending) save.mutate(choice.wanted);
        }}
      >
        <Stack gap={FIELD_GAP}>
          <Text size="sm" c="dimmed">
            Кап держит наш расход юнитов Ahrefs за цикл ключа: прогон, который не помещается в
            остаток капа, не запускается, и обход Этапа 2 не покупает DR сверх него. Новый кап
            действует сразу.
          </Text>
          {save.error ? (
            <Alert color="red" title="Кап не сменили">
              {refusalOf(save.error)}
            </Alert>
          ) : null}
          <CapChoice ahrefsKey={key} choice={choice} onEdit={() => save.reset()} />
          {key.changes.length > 0 && <RecentChanges changes={key.changes} />}
          {/* Почему «Сохранить» заперта — строкой под кнопками; за границей причина — уже под
              полем. */}
          <SaveActions
            onCancel={onClose}
            saving={save.isPending}
            disabled={!choice.ready}
            locked={choice.same ? 'Это действующий кап — выберите другой' : null}
          />
        </Stack>
      </form>
    </Modal>
  );
}

/** Последние смены — что ставили, кто и когда: решая, видно, откуда пришло нынешнее. */
function RecentChanges({ changes }: { changes: CapChange[] }) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        Последние смены
      </Text>
      {changes.map((change) => (
        <Text key={change.id} size="sm" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
          <b>{capWords(change.units_cap)}</b> — {changedBy(change)}
        </Text>
      ))}
    </Stack>
  );
}
