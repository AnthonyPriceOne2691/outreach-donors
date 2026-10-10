/**
 * Сборка очереди на «Письмах»: кампания и числа, прогоны, текст первого письма — и кнопка.
 *
 * **Пока в очереди есть письма, форма свёрнута в строку** (аудит экранов 09.10.2026).
 * Раскрытая, она стояла над очередью при каждом заходе (очередь доноров начиналась с
 * 683 px на 1440 и с 1139 px на телефоне, свёрнутая — с 520 и 776), а работа здесь —
 * читать и отправлять собранное; собирают раз в несколько дней. У пустой очереди форма
 * раскрыта: собрать — следующее, что здесь делают. Раскрытую руками форму экран не
 * сворачивает, пока не сменят адресатов.
 *
 * **Порядок формы — порядок решения:** кампания и числа, прогоны, текст первого письма,
 * и кнопка — последней; у выключенной кнопки сказано, чего не хватает (`buildBlocked`).
 * Поля — по значению, пояснения — в «i».
 *
 * **Числа — те, что уйдут** (`CountInput`): пустое поле при уходе из него показывает
 * умолчание, число вне границ — границу. Границы те же, что у сервера: добивка — через
 * 1–90 дней, «через 0 дней» значило бы «сразу» (аудит 10.10.2026).
 */

import { Button, Collapse, Group, Stack, Text, TextInput } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useId, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { LetterDraft, LettersView } from '../api/types';
import { HintLabel } from '../components/HintLabel';
import { CountInput } from './CountInput';
import { LetterDraftEditor, draftOf } from './LetterDraftEditor';
import { RunPicker, useAcceptedRuns } from './RunPicker';
import type { LetterTarget } from './targets';
import { ABOUT } from './targets';

/** Писем за сборку, пока человек не поправил, и потолок — как у сервера (`LIMIT_MAX`). */
export const LIMIT_DEFAULT = 50;
const LIMIT_MAX = 500;

/** Срок добивки, в днях, — как у сервера (`letters/chain.py`). */
const FOLLOWUP_MIN = 1;
const FOLLOWUP_MAX = 90;

/** Сроки добивок, которые уйдут со сборкой: поправленные — как в поле, нетронутые —
 *  умолчание сервера, которое поле и показывает. Нуля здесь не бывает: прежнее «?? 0»
 *  вместо неизвестного умолчания значило «добивка сразу вслед за письмом» (аудит
 *  10.10.2026). Срока нет — цепочка на нём кончается, как и на сервере. */
export function followupDays(followups: (number | null)[], defaults: number[]): number[] {
  const days: number[] = [];
  for (const [index, set] of followups.entries()) {
    const day = set ?? defaults[index];
    if (day === undefined || day < FOLLOWUP_MIN) break;
    days.push(day);
  }
  return days;
}

/** Чего не хватает для сборки — словами у кнопки; `null` — собрать можно.
 *
 *  Принятых доноров нет вовсе — так и сказано: отмечать нечего, и «Отметьте прогоны
 *  рассылки» под строкой «Принятых доноров ещё нет» спорило с ней (проверка QA
 *  10.10.2026). `accepted` — сколько прогонов с принятыми; пока список не пришёл
 *  (`undefined`), о нём не говорится ничего. */
export function buildBlocked(
  target: LetterTarget,
  campaign: string,
  runIds: number[],
  accepted: number | undefined,
): string | null {
  const donors = target === 'donors';
  if (donors && accepted === 0) return 'Собирать не из чего — принятых доноров нет';
  if (campaign.trim() === '') return 'Назовите кампанию';
  if (donors && runIds.length === 0) return 'Отметьте прогоны рассылки';
  return null;
}

export interface BuildProps {
  view: LettersView;
  target: LetterTarget;
  campaign: string;
  onCampaign: (value: string) => void;
  limit: number;
  onLimit: (value: number) => void;
  followups: (number | null)[];
  onFollowups: Dispatch<SetStateAction<(number | null)[]>>;
  building: boolean;
  onBuild: () => void;
  runIds: number[];
  onRunIds: (next: number[]) => void;
  letterEdit: LetterDraft | null;
  onLetterEdit: (next: LetterDraft) => void;
}

export function BuildForm({
  view,
  target,
  campaign,
  onCampaign,
  limit,
  onLimit,
  followups,
  onFollowups,
  building,
  onBuild,
  runIds,
  onRunIds,
  letterEdit,
  onLetterEdit,
}: BuildProps) {
  const formId = useId();
  const queued = view.letters.length > 0;
  const accepted = useAcceptedRuns(target === 'donors');
  const blocked = buildBlocked(target, campaign, runIds, accepted.data?.length);
  const [opened, setOpened] = useState(false);
  // Пустая очередь — форма раскрыта, и строки «Сборка очереди» нет: сворачивать нечего,
  // а строка над формой стоила бы 40 px. Раскрытая руками остаётся раскрытой и после
  // того, как очередь пришла.
  const shown = !queued || opened;
  const defaultDays = view.followup_default;
  const letterDefault = view.letter_default;

  return (
    <Stack gap="xs">
      {queued && (
        <Group gap="xs" align="center">
          {/* `aria-expanded` — состояние для программы чтения с экрана и для теста:
              в jsdom анимация высоты не проигрывается. */}
          <Button
            variant="subtle"
            size="compact-sm"
            px={6}
            leftSection={
              <IconChevronDown
                size={16}
                style={{
                  transform: shown ? 'rotate(180deg)' : 'none',
                  transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
                }}
              />
            }
            aria-expanded={shown}
            aria-controls={formId}
            onClick={() => setOpened(!opened)}
          >
            Сборка очереди
          </Button>
          {!shown && (
            <Text size="sm" c="dimmed">
              {target === 'donors'
                ? 'кампания, прогоны, текст первого письма'
                : 'кампания и текст первого письма'}
            </Text>
          )}
        </Group>
      )}

      <Collapse id={formId} in={shown} transitionDuration={220} transitionTimingFunction="ease">
        <Stack gap="sm" pt={queued ? 4 : 0}>
          <Group align="flex-end" gap="sm">
            <TextInput
              labelProps={{ labelElement: 'div' }}
              label={
                <HintLabel
                  label="Кампания"
                  hint="Одноимённая дополняется, а не заводится второй раз."
                />
              }
              aria-label="Кампания"
              placeholder={ABOUT[target].placeholder}
              value={campaign}
              w={280}
              onChange={(event) => onCampaign(event.currentTarget.value)}
            />
            <CountInput
              labelProps={{ labelElement: 'div' }}
              label={
                <HintLabel
                  label="За раз"
                  hint={`Писем за одну сборку, от 1 до ${LIMIT_MAX}. Каждое стоит вызова модели.`}
                />
              }
              aria-label="Писем за раз"
              value={limit}
              fallback={LIMIT_DEFAULT}
              min={1}
              max={LIMIT_MAX}
              w="6.5rem"
              onValue={onLimit}
            />
            {defaultDays.map((fallback, index) => (
              <CountInput
                key={index}
                labelProps={{ labelElement: 'div' }}
                label={
                  <HintLabel
                    label={`Добивка ${index + 1}`}
                    hint={`Через сколько дней после ${index === 0 ? 'первого письма' : 'предыдущей добивки'}: от ${FOLLOWUP_MIN} до ${FOLLOWUP_MAX}.`}
                  />
                }
                aria-label={`Добивка ${index + 1}, дней`}
                suffix=" дн."
                value={followups[index] ?? fallback}
                fallback={fallback}
                min={FOLLOWUP_MIN}
                max={FOLLOWUP_MAX}
                w="7rem"
                onValue={(days) =>
                  onFollowups((was) => was.map((old, at) => (at === index ? days : old)))
                }
              />
            ))}
          </Group>

          {target === 'donors' ? <RunPicker value={runIds} onChange={onRunIds} /> : null}

          <LetterDraftEditor
            key={target}
            target={target}
            fallback={letterDefault}
            value={letterEdit ?? draftOf(letterDefault)}
            onChange={onLetterEdit}
          />

          <BuildButton building={building} blocked={blocked} onBuild={onBuild} />
        </Stack>
      </Collapse>
    </Stack>
  );
}

/** «Собрать очередь», а у выключенной — чего не хватает: строкой рядом, а не подсказкой
 *  по наведению (на телефоне наведения нет). Строка — и описание кнопки для диктора. */
function BuildButton({
  building,
  blocked,
  onBuild,
}: {
  building: boolean;
  blocked: string | null;
  onBuild: () => void;
}) {
  const reasonId = useId();
  return (
    <Group gap="sm">
      <Button
        color="lagoon"
        className="press"
        loading={building}
        disabled={blocked !== null}
        aria-describedby={blocked !== null ? reasonId : undefined}
        onClick={onBuild}
      >
        Собрать очередь
      </Button>
      {blocked !== null && (
        <Text id={reasonId} size="sm" c="dimmed">
          {blocked}
        </Text>
      )}
    </Group>
  );
}
