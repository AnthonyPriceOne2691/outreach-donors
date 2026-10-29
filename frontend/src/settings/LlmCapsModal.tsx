/**
 * «Изменить потолки» модели на «Расходе»: общий потолок на весь сервис — числом из списка или
 * «без потолка», и лимиты разделов процентами общего — «добавить из остатка» и «убавить»
 * шагом сервера (слово Anthony 29.10.2026, устройство — как ключи в пространстве Ahrefs).
 *
 * **Свободный остаток — что не роздано**: раздать можно не больше 100 %, и «добавить» заперта,
 * когда остатка меньше шага; «убавить» возвращает в остаток, но не ниже шага — ноль не лимит, а
 * запрет вызовов раздела. Числа рядом с процентами — от выбранного общего: общий двигают —
 * лимиты растут вместе с ним. Границы — сервера, и последнее слово за ним: отказ — над полями,
 * выбранное остаётся (правило форм).
 *
 * **«Без потолка» — с предупреждением**: расход модели тогда не держит ничто; лимиты разделов
 * запоминаются и встают обратно, когда общий снова число.
 *
 * **Окно открывается на сохранённом блоке**, а не сохраняли — на предложенном сервером (общий
 * 50 млн, продажи 20 % — те же 10 млн, что сейчас, агент 20 %, аутрич 30 %, свободно 30 %).
 * Раздел, которого учётка не видит (продажи без права «Продажи»), в окне не меняется.
 */

import { Alert, Button, Group, Modal, Select, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { saveLlmCaps } from '../api/llmCaps';
import type { LlmBlock, LlmCaps, LlmCapChange, LlmPart } from '../api/llmCaps';
import { FIELD_GAP } from '../components/formRhythm';
import { SaveActions } from '../components/SaveActions';
import { formatNumber } from '../format';
import { notify } from '../notices';

/** «50 000 000 токенов в сутки». */
export function tokensWords(tokens: number): string {
  return `${formatNumber(tokens)} токенов в сутки`;
}

/** Значение списка «Общий»: число токенов строкой или «без потолка». */
const NO_CAP = 'none';

type Shares = Record<LlmPart, number>;

function sharesOf(block: LlmBlock): Shares {
  return { sales: block.sales_share, agent: block.agent_share, outreach: block.outreach_share };
}

function blockOf(general: number | null, shares: Shares): LlmBlock {
  return {
    general_tokens: general,
    sales_share: shares.sales,
    agent_share: shares.agent,
    outreach_share: shares.outreach,
  };
}

function sameBlock(a: LlmBlock, b: LlmBlock | null): boolean {
  return (
    b !== null &&
    a.general_tokens === b.general_tokens &&
    a.sales_share === b.sales_share &&
    a.agent_share === b.agent_share &&
    a.outreach_share === b.outreach_share
  );
}

/** Уведомление после смены — словами того, что поставили. */
function savedWords(change: LlmCapChange): string {
  const general = change.block.general_tokens;
  return general === null
    ? 'Потолки модели сняты: расход не держит ничто. Лимиты разделов запомнены.'
    : `Общий потолок — ${tokensWords(general)}; лимиты разделов действуют с этой минуты.`;
}

/** Часть от общего токенами — для подписи рядом с процентами. */
function tokensOf(general: number | null, share: number): string {
  return general === null ? '' : ` · ${formatNumber(Math.floor((general * share) / 100))} токенов`;
}

interface Props {
  caps: LlmCaps;
  onClose: () => void;
}

export function LlmCapsModal({ caps, onClose }: Props) {
  const queryClient = useQueryClient();
  const start = caps.saved ?? caps.suggested;
  const [general, setGeneral] = useState<number | null>(start.general_tokens);
  const [shares, setShares] = useState<Shares>(() => sharesOf(start));
  const step = caps.share_step;
  const free = 100 - shares.sales - shares.agent - shares.outreach;
  const wanted = blockOf(general, shares);
  const same = sameBlock(wanted, caps.saved);
  const save = useMutation({
    mutationFn: (block: LlmBlock) => saveLlmCaps(block),
    onSuccess: async (change) => {
      // Потолки читают «Расход» и сметы прогонов; смету человек считает заново сам.
      await queryClient.invalidateQueries({ queryKey: ['usage'] });
      notify({ message: savedWords(change), color: 'green' });
      onClose();
    },
  });
  const move = (part: LlmPart, by: number) => {
    setShares((now) => ({ ...now, [part]: now[part] + by }));
    save.reset();
  };

  return (
    <Modal opened onClose={onClose} title="Потолки модели">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!same && !save.isPending) save.mutate(wanted);
        }}
      >
        <Stack gap={FIELD_GAP}>
          <Text size="sm" c="dimmed">
            Общий держит расход модели на весь сервис за сутки. У каждого раздела — свой лимит
            процентом общего, жёсткий: потраченное другими он не берёт, а свободный остаток не
            тратит никто, пока его не добавят разделу. Новые потолки действуют сразу.
          </Text>
          {caps.scheme === 'env' && (
            <Text size="sm">
              Сейчас потолки — из настроек сервера. Первое сохранение включит лимиты разделов; ниже
              — предложенные значения.
            </Text>
          )}
          {save.error ? (
            <Alert color="red" title="Потолки не сменили">
              {refusalOf(save.error)}
            </Alert>
          ) : null}
          <Select
            label="Общий потолок на весь сервис"
            allowDeselect={false}
            data={[
              ...caps.general_presets.map((tokens) => ({
                value: String(tokens),
                label: `${formatNumber(tokens / 1_000_000)} млн токенов в сутки`,
              })),
              { value: NO_CAP, label: 'Без потолка' },
            ]}
            value={general === null ? NO_CAP : String(general)}
            onChange={(next) => {
              setGeneral(next === null || next === NO_CAP ? null : Number(next));
              save.reset();
            }}
          />
          {general === null && (
            <Alert color="yellow" title="Без потолка — настройка с ответственностью">
              Расход модели за сутки не держит ничто: ни разделы, ни общий. Лимиты разделов
              запомнятся и встанут обратно, когда общий снова будет числом. Смена попадёт в журнал.
            </Alert>
          )}
          <Text size="sm">
            Свободный остаток <b>{free}%</b>
            {tokensOf(general, free)}
          </Text>
          {caps.parts.map((part) => (
            <Group key={part.part} justify="space-between" gap="sm" wrap="wrap">
              <Text size="sm">
                {part.title} — <b>{shares[part.part]}%</b>
                {tokensOf(general, shares[part.part])}
              </Text>
              <Group gap="xs">
                <Button
                  size="compact-sm"
                  variant="default"
                  className="press"
                  aria-label={`Убавить разделу «${part.title}»`}
                  disabled={shares[part.part] <= step}
                  onClick={() => move(part.part, -step)}
                >
                  Убавить
                </Button>
                <Button
                  size="compact-sm"
                  variant="default"
                  className="press"
                  aria-label={`Добавить разделу «${part.title}» из остатка`}
                  disabled={free < step}
                  onClick={() => move(part.part, step)}
                >
                  Добавить из остатка
                </Button>
              </Group>
            </Group>
          ))}
          <SaveActions
            onCancel={onClose}
            saving={save.isPending}
            locked={same ? 'Это действующие потолки — измените общий или лимит раздела' : null}
          />
        </Stack>
      </form>
    </Modal>
  );
}
