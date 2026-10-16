/**
 * «Изменить кап» на экране «Расход»: новый месячный кап юнитов Ahrefs — одним полем.
 *
 * Решение владельца 16.10.2026: кап меняют здесь, а не правкой окружения на сервере; видно,
 * кто и когда менял. Смета прогона с «не помещается» ведёт сюда же.
 *
 * **Границы — сервера** (`ahrefs_cap_limits`): от единицы до лимита тарифа. Поле проверяется
 * ими до нажатия — за границей под полем словами, почему нельзя, и «Сохранить» заперта; своей
 * копии чисел у экрана нет. Лимит тарифа назван под полем: это то, во что кап упирается, и в
 * окне на одно поле строке под ним место есть.
 *
 * **Отказ сервера — над полем, вписанное остаётся** (правило форм, проверка QA 10.10.2026):
 * уведомление в углу закрывало бы кнопки окна.
 *
 * **Окно открывается чистым** — на действующем капе: экран монтирует его заново на каждое
 * открытие (`AhrefsCap`), как «Больше не писать» на «Стоп-листе».
 */

import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { saveAhrefsCap } from '../api/settings';
import type { CapChange } from '../api/settings';
import type { ThresholdRange } from '../api/types';
import { ACTIONS_GAP, FIELD_GAP } from '../components/formRhythm';
import { NumberField } from '../components/NumberField';
import { numberRefusal, numberText, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatDateTime, formatNumber } from '../format';
import { notify } from '../notices';

/** Поле — по числу «1 000 000», а не на ширину окна. */
const INPUT_WIDTH = '9rem';

/** Кто и когда сменил кап: «кап@site.com · 16.10.2026, 14:05»; учётку удалили — только когда. */
export function changedBy(change: CapChange): string {
  const when = formatDateTime(change.set_at);
  return change.set_by === null ? when : `${change.set_by} · ${when}`;
}

function ruleOf(limits: ThresholdRange): NumberRule {
  return { decimals: 0, min: limits.min, max: limits.max };
}

/** Что не так с вписанным — словами; `null` — годится. Пусто — «впишите число», а не ноль:
 *  ноль — тоже кап, и его никто не набирал. */
export function capRefusal(text: string, limits: ThresholdRange): string | null {
  if (text.trim() === '') return 'Впишите число';
  return numberRefusal(text, ruleOf(limits));
}

interface Props {
  /** Действующий кап — с него окно и начинается. */
  cap: number;
  limits: ThresholdRange;
  changes: CapChange[];
  onClose: () => void;
}

export function AhrefsCapModal({ cap, limits, changes, onClose }: Props) {
  const queryClient = useQueryClient();
  const [text, setText] = useState(() => numberText(cap));
  const typed = validNumber(text, ruleOf(limits));
  const save = useMutation({
    mutationFn: (unitsCap: number) => saveAhrefsCap(unitsCap),
    onSuccess: async (change) => {
      // Кап читают «Расход» и «Обзор»; смету прогона человек считает заново сам.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['usage'] }),
        queryClient.invalidateQueries({ queryKey: ['overview'] }),
      ]);
      notify({
        message: `Кап — ${formatNumber(change.units_cap)} юнитов в месяц. Сметы и потолки прогонов считают от него с этой минуты.`,
        color: 'green',
      });
      onClose();
    },
  });
  const same = typed === cap;

  return (
    <Modal opened onClose={onClose} title="Месячный кап Ahrefs">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (typed !== null && !same && !save.isPending) save.mutate(typed);
        }}
      >
        <Stack gap={FIELD_GAP}>
          <Text size="sm" c="dimmed">
            Кап держит наш расход юнитов Ahrefs за месяц: прогон, который не помещается в остаток
            капа, не запускается, и обход Этапа 2 не покупает DR сверх него. Новый кап действует
            сразу.
          </Text>
          {save.error ? (
            <Alert color="red" title="Кап не сменили">
              {refusalOf(save.error)}
            </Alert>
          ) : null}
          <NumberField
            label="Юнитов в месяц"
            description={`Не больше лимита тарифа — ${formatNumber(limits.max)}`}
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
          {changes.length > 0 && <RecentChanges changes={changes} />}
          <Stack gap={6} mt={ACTIONS_GAP}>
            <Group justify="flex-end" gap="sm">
              <Button variant="default" className="press" onClick={onClose}>
                Отмена
              </Button>
              <Button
                type="submit"
                className="press"
                loading={save.isPending}
                disabled={typed === null || same}
              >
                Сохранить
              </Button>
            </Group>
            {/* Почему «Сохранить» заперта — строкой под кнопками (правило 10.10.2026), а не
                в одном ряду с ними: в ряд окна 440 px они не вставали, и «Сохранить» уезжала
                на вторую строку (снимок стенда). За границей причина — уже под полем. */}
            {same && (
              <Text size="sm" c="dimmed" ta="right">
                Это действующий кап — впишите другой
              </Text>
            )}
          </Stack>
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
          <b>{formatNumber(change.units_cap)}</b> — {changedBy(change)}
        </Text>
      ))}
    </Stack>
  );
}
