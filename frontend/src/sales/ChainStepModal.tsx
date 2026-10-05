/**
 * Окно шага цепочки: правка шаблона и письмо глазами адресата.
 *
 * **Текст — в формате зон, как у шаблонов доноров**: строка «[имя] rewrite»
 * открывает зону, которую модель переписывает под адресата, «[имя] fixed» — зону,
 * которая уходит как есть. Подпись и физический адрес в шаблон не пишут: их
 * допишет сборка из «Отправителя», и предпросмотр показывает их на своём месте.
 *
 * **Проверяет сервер**: «Re:» в теме, незнакомую подстановку, метрики, устройство
 * зон — теми же правилами, что загрузку файла. До нажатия экран ловит только
 * пустое и длинное (`chainDraft.ts`); отказ сервера встаёт над формой словами, а
 * введённое остаётся на месте. «Показать письмо» не пишет ничего; правка после
 * показа убирает показанное — письмо по прежнему тексту врало бы.
 */

import {
  Alert,
  Button,
  Group,
  Modal,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import {
  CHAIN_STEPS,
  chainLanguageTitle,
  chainPlaceholderTitle,
  chainStepTitle,
} from '../api/salesLabels';
import type { ChainStepCard, ChainView } from '../api/salesTypes';
import { ChainLetter } from './ChainLetter';
import { changed, draftOf, FIRST_STEP, previewBodyOf, refusalsOf, stepBodyOf } from './chainDraft';
import type { ChainDraft, StepPlace } from './chainDraft';
import { usePreview, useSaveStep } from './chainData';
import { SaveRefusal } from './SaveRefusal';

interface Props {
  place: StepPlace;
  /** Набор словами: «общий набор» или «гипотеза «…»». */
  setTitle: string;
  /** Шаблон шага; ещё не задан — `null`. */
  row: ChainStepCard | null;
  view: ChainView;
  onClose: () => void;
}

/** Что можно писать в тексте этого шага — словами, с подстановками сервера. */
function bodyHint(step: number, placeholders: string[]): string {
  const known = placeholders
    .map((name) => `{{${name}}} — ${chainPlaceholderTitle(name)}`)
    .join(', ');
  const zones =
    step === FIRST_STEP
      ? '«[имя] rewrite» — модель переписывает под адресата, «[имя] fixed» — уходит как есть'
      : 'только «[имя] fixed» — модель в добивках не участвует';
  return `Зоны: ${zones}. Подстановки: ${known}. Подпись и адрес не пишите — их допишет сборка из «Отправителя».`;
}

interface FieldsProps {
  place: StepPlace;
  draft: ChainDraft;
  refusals: ReturnType<typeof refusalsOf>;
  placeholders: string[];
  onChange: (patch: Partial<ChainDraft>) => void;
}

function StepFields({ place, draft, refusals, placeholders, onChange }: FieldsProps) {
  return (
    <>
      {place.step === FIRST_STEP && (
        <TextInput
          label="Тема"
          description="без «Re:» и «Fwd:» — переписки ещё нет; подстановки — как в тексте"
          value={draft.subject}
          error={refusals.subject}
          onChange={(event) => onChange({ subject: event.currentTarget.value })}
        />
      )}
      <Textarea
        label="Текст письма"
        description={bodyHint(place.step, placeholders)}
        placeholder={'[greeting] rewrite\n…\n\n[offer] fixed\n…'}
        autosize
        minRows={8}
        maxRows={18}
        classNames={{ input: 'chainBody' }}
        value={draft.body}
        error={refusals.body}
        onChange={(event) => onChange({ body: event.currentTarget.value })}
      />
      <Switch
        label="Шаг включён — входит в цепочку"
        checked={draft.active}
        onChange={(event) => onChange({ active: event.currentTarget.checked })}
      />
    </>
  );
}

export function ChainStepModal({ place, setTitle, row, view, onClose }: Props) {
  const [draft, setDraft] = useState<ChainDraft>(() => draftOf(row));
  // Тронутые поля: пустая новая форма не краснеет сразу.
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const save = useSaveStep(onClose);
  const preview = usePreview();
  const refusals = refusalsOf(draft, place.step, view.limits);
  const clean = Object.keys(refusals).length === 0;
  const shown = Object.fromEntries(
    Object.entries(refusals).filter(([field]) => touched.has(field)),
  ) as typeof refusals;
  const title = `${chainStepTitle(place.step)} · ${chainLanguageTitle(place.language)} — ${setTitle}`;

  return (
    <Modal opened onClose={onClose} size="xl" title={title}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (clean && changed(draft, row)) save.mutate(stepBodyOf(draft, place));
        }}
      >
        <Stack gap="sm">
          <SaveRefusal error={save.error} />
          <Text size="sm" c="dimmed">
            {CHAIN_STEPS[place.step]?.hint}
          </Text>
          <StepFields
            place={place}
            draft={draft}
            refusals={shown}
            placeholders={view.placeholders}
            onChange={(patch) => {
              setDraft((current) => ({ ...current, ...patch }));
              setTouched((current) => new Set([...current, ...Object.keys(patch)]));
              preview.reset();
            }}
          />
          <Group justify="space-between" mt="sm" gap="xs">
            <Button
              variant="default"
              className="press"
              disabled={!clean}
              loading={preview.isPending}
              onClick={() => preview.mutate(previewBodyOf(draft, place))}
            >
              Показать письмо
            </Button>
            <Group gap="xs">
              <Button variant="subtle" className="press" onClick={onClose}>
                Отмена
              </Button>
              <Button
                type="submit"
                className="press"
                disabled={!clean || !changed(draft, row)}
                loading={save.isPending}
              >
                Сохранить
              </Button>
            </Group>
          </Group>
          {preview.error !== null && (
            <Alert color="red" title="Письмо не собралось">
              {refusalOf(preview.error)}
            </Alert>
          )}
          {preview.data !== undefined && <ChainLetter letter={preview.data} />}
        </Stack>
      </form>
    </Modal>
  );
}
