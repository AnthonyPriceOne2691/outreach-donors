/**
 * Цепочка писем продаж: первое письмо и две добивки в той же переписке — на каждом языке.
 *
 * **Тексты живут только здесь**, в базе: репозиторий публичный, умолчаний в коде нет.
 * Незаданная цепочка так и называется — «цепочка не задана», и сборка писем на этом
 * языке отказывает словами. Первичное наполнение — файлом через консоль
 * (`outreach sales-chain-load`), правка — здесь.
 *
 * **Набор — общий или гипотезы.** Своя цепочка гипотезы на языке заменяет общую
 * целиком, если у гипотезы есть хоть один включённый свой шаг; иначе действует
 * общая. Какая цепочка действует и полна ли она, называет сервер — экран не решает
 * сам: правило выбора одно, и живёт оно там, где по нему соберут письма. Набор —
 * гипотеза раздела из адреса (`?hypothesis=`): общий — гипотеза не выбрана.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { chainLanguageTitle, chainStepTitle } from '../api/salesLabels';
import type { ChainState, ChainStepCard, ChainView, HypothesisCard } from '../api/salesTypes';
import { InfoHint } from '../components/InfoHint';
import { formatDateTime } from '../format';
import { useChain } from './chainData';
import type { StepPlace } from './chainDraft';
import { ChainStepModal } from './ChainStepModal';
import { useMayWrite, WriteRight } from './WriteRight';

const COMMON = 'common';

interface Props {
  hypotheses: HypothesisCard[];
}

interface OwnerProps extends Props {
  /** Чей набор: гипотеза раздела или `null` — общий. */
  owner: number | null;
  onOwner: (owner: number | null) => void;
}

function setTitleOf(hypotheses: HypothesisCard[], owner: number | null): string {
  if (owner === null) return 'общий набор';
  const name = hypotheses.find((hypothesis) => hypothesis.id === owner)?.name;
  return `гипотеза «${name ?? `№${owner}`}»`;
}

function ChainHead({ hypotheses, owner, onOwner }: OwnerProps) {
  const options = [
    { value: COMMON, label: 'Общий — для всех гипотез' },
    ...hypotheses.map((hypothesis) => ({ value: String(hypothesis.id), label: hypothesis.name })),
  ];
  return (
    <Group justify="space-between" align="flex-end" wrap="wrap" gap="md" px="md">
      {/* Вступление — одной строкой, остальное — в «i» (аудит экранов 09.10.2026). */}
      <Group gap={4} wrap="nowrap" align="flex-start" style={{ flex: '1 1 20rem', minWidth: 0 }}>
        <Text size="sm">Первое письмо и две добивки в той же переписке — на каждом языке.</Text>
        <InfoHint name="Где живут тексты и чья цепочка действует" width={340}>
          Тексты живут только здесь, в базе. Подпись и физический адрес допишет сборка из
          «Отправителя» — в шаблоне их нет. Своя цепочка гипотезы на языке заменяет общую целиком,
          если у гипотезы есть хоть один включённый свой шаг.
        </InfoHint>
      </Group>
      <Select
        label="Набор"
        allowDeselect={false}
        data={options}
        value={owner === null ? COMMON : String(owner)}
        onChange={(value) => onOwner(value === null || value === COMMON ? null : Number(value))}
        w={{ base: '100%', xs: 280 }}
      />
    </Group>
  );
}

/** Что с цепочкой языка: полна, неполна или не задана — и чья она. */
function chainWords(state: ChainState, owner: number | null): { badge: string; line: string } {
  const borrowed =
    owner !== null && state.source === 'common'
      ? 'Своих включённых шагов на этом языке нет — действует общая цепочка. '
      : '';
  if (state.missing.length === 0) {
    return { badge: 'цепочка полна', line: `${borrowed}Версия ${state.version}.` };
  }
  const badge = state.missing.length === 3 ? 'цепочка не задана' : 'цепочка неполна';
  const line = `${borrowed}Нет ${state.missing.join(', ')} — письма продаж на этом языке не соберутся.`;
  return { badge, line };
}

function StepState({ row }: { row: ChainStepCard | undefined }) {
  if (row === undefined) {
    return (
      <Badge variant="light" color="yellow">
        не задан
      </Badge>
    );
  }
  return (
    <Badge variant="light" color={row.active ? 'green' : 'gray'}>
      {row.active ? 'задан' : 'выключен'}
    </Badge>
  );
}

/** Кнопка шага. Без права записи заданный шаг открывают смотреть и показать письмо, а задать
 *  новый нельзя (`WriteRight`). */
function stepAction(row: ChainStepCard | undefined, mayWrite: boolean): string {
  if (row === undefined) return 'Задать';
  return mayWrite ? 'Править' : 'Открыть';
}

function StepRow({
  step,
  row,
  mayWrite,
  onEdit,
}: {
  step: number;
  row: ChainStepCard | undefined;
  mayWrite: boolean;
  onEdit: () => void;
}) {
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm" className="chainStep">
      <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
        <Group gap="xs">
          <Text size="sm" fw={500} className="chainStepTitle">
            {step}. {chainStepTitle(step)}
          </Text>
          <StepState row={row} />
        </Group>
        {row?.subject ? (
          <Text size="sm" className="chainSubject">
            Тема: {row.subject}
          </Text>
        ) : null}
        {row !== undefined && (
          <>
            <Text size="xs" c="dimmed" lineClamp={2} className="chainExcerpt">
              {row.zones.map((zone) => zone.text).join(' · ')}
            </Text>
            <Text size="xs" c="dimmed" className="chainWho">
              {row.updated_by ?? '—'} · {formatDateTime(row.updated_at)}
            </Text>
          </>
        )}
      </Stack>
      <Button
        size="compact-sm"
        variant="default"
        className="press"
        disabled={row === undefined && !mayWrite}
        onClick={onEdit}
      >
        {stepAction(row, mayWrite)}
      </Button>
    </Group>
  );
}

function LanguageCard({
  view,
  state,
  owner,
  mayWrite,
  onEdit,
}: {
  view: ChainView;
  state: ChainState;
  owner: number | null;
  mayWrite: boolean;
  onEdit: (step: number) => void;
}) {
  const words = chainWords(state, owner);
  const rowOf = (step: number) =>
    view.rows.find((row) => row.step === step && row.language === state.language);
  return (
    <Card
      component="section"
      className="glassQuiet"
      p="md"
      aria-label={chainLanguageTitle(state.language)}
    >
      <Stack gap="sm">
        <Group justify="space-between" gap="xs">
          <Title order={5}>{chainLanguageTitle(state.language)}</Title>
          <Badge variant="light" color={state.missing.length === 0 ? 'green' : 'yellow'}>
            {words.badge}
          </Badge>
        </Group>
        <Text size="xs" c="dimmed" className="chainState">
          {words.line}
        </Text>
        {view.steps.map((step) => (
          <StepRow
            key={step}
            step={step}
            row={rowOf(step)}
            mayWrite={mayWrite}
            onEdit={() => onEdit(step)}
          />
        ))}
      </Stack>
    </Card>
  );
}

function ChainEmpty() {
  return (
    <Text size="sm" c="dimmed" px="md">
      Шаблонов в наборе нет. Задайте шаги здесь; набор целиком из файла загружает администратор.
    </Text>
  );
}

function ChainWaiting({ error }: { error: unknown }) {
  if (!error) return <Loader aria-label="Загружаем цепочку писем" m="md" />;
  return (
    <Alert color="red" title="Цепочка писем не загрузилась">
      {refusalOf(error)}
    </Alert>
  );
}

export function ChainPane({ hypotheses, owner, onOwner }: OwnerProps) {
  const [editing, setEditing] = useState<StepPlace | null>(null);
  const mayWrite = useMayWrite();
  const chain = useChain(owner);
  const view = chain.data;
  const setTitle = setTitleOf(hypotheses, owner);
  const rowAt = (place: StepPlace) =>
    view?.rows.find((row) => row.step === place.step && row.language === place.language);

  return (
    <Stack gap="sm">
      <ChainHead hypotheses={hypotheses} owner={owner} onOwner={onOwner} />
      {!mayWrite && <WriteRight what="Правит цепочку" px="md" />}
      {view === undefined ? (
        <ChainWaiting error={chain.error} />
      ) : (
        <>
          {view.rows.length === 0 && <ChainEmpty />}
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
            {view.chains.map((state) => (
              <LanguageCard
                key={state.language}
                view={view}
                state={state}
                owner={owner}
                mayWrite={mayWrite}
                onEdit={(step) =>
                  setEditing({ hypothesisId: owner, step, language: state.language })
                }
              />
            ))}
          </SimpleGrid>
        </>
      )}
      {editing !== null && view !== undefined && (
        <ChainStepModal
          key={`${editing.hypothesisId ?? COMMON}-${editing.step}-${editing.language}`}
          place={editing}
          setTitle={setTitle}
          row={rowAt(editing) ?? null}
          view={view}
          mayWrite={mayWrite}
          onClose={() => setEditing(null)}
        />
      )}
    </Stack>
  );
}
