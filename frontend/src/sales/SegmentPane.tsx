/**
 * Вкладка «Сбор базы» — прогон гипотезы: ключи → выдача → судья сегмента → адрес → лиды.
 *
 * ТЗ, «Что получаем», п. 1: сбор новых сайтов по ключевым словам, ИИ проверяет, подходит ли
 * сайт под сегмент, дубли, стоп-лист и мёртвые адреса отсекаются до первого письма. Здесь:
 *
 * - **описание сегмента гипотезы** — его читает судья; правка — новый вопрос модели, прежние
 *   вердикты по нему не годятся. Без описания прогон не запускается (судье не с чем сравнивать).
 *   На экране — три строки и «показать целиком» (волна 15: абзац описания стоял над отчётом при
 *   каждом заходе); кнопка — только когда описание в три строки не помещается, и мерится это в
 *   браузере, по ширине окна, а не числом букв;
 * - **новый прогон** — смета до запуска словами сервера (`SegmentForm`); у гипотезы с прогонами
 *   форма свёрнута в кнопку;
 * - **прогоны гипотезы** — последний сразу, прежние — списком со стадией каждого; список
 *   опрашивается, пока есть идущий прогон (`useSegment`); отчёт и решение человека по
 *   «на проверку» (`SegmentReport`).
 *
 * Гипотеза — та же, что у остальных вкладок раздела (`?hypothesis=`); выбранный прогон — в
 * адресе (`?run=N`): «назад» и присланная ссылка открывают тот же прогон.
 */

import {
  Alert,
  Button,
  Card,
  Group,
  Loader,
  Select,
  Stack,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useId, useLayoutEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import type { HypothesisCard } from '../api/salesTypes';
import { InfoHint } from '../components/InfoHint';
import { dropdownBelow } from '../theme';
import { HYPOTHESES_QUERY_KEY } from './hypothesisData';
import { writeSegment } from './segmentApi';
import type { SegmentHome, SegmentRunRow } from './segmentApi';
import { runTitle, SEGMENT_QUERY_KEY, useSegment } from './segmentData';
import { SegmentForm } from './SegmentForm';
import { SegmentHint } from './SegmentHint';
import { SegmentReport } from './SegmentReport';
import { runState } from './segmentStage';

/** Строк описания сегмента до «показать целиком». */
export const SEGMENT_LINES = 3;

/** Описание сегмента — три строки; не поместилось — «показать целиком». Поместилось ли, мерит
 *  браузер (высота текста против высоты трёх строк) и перемеряет при смене ширины: число букв
 *  этого не скажет — на 1440 и на 390 строк разное число. */
function SegmentText({ text }: { text: string }) {
  const textId = useId();
  const ref = useRef<HTMLParagraphElement>(null);
  const [whole, setWhole] = useState(false);
  const [long, setLong] = useState(false);
  useLayoutEffect(() => {
    const node = ref.current;
    if (node === null || whole) return undefined;
    const measure = () => setLong(node.scrollHeight > node.clientHeight + 1);
    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [text, whole]);
  return (
    <Stack gap={2} align="flex-start">
      <Text
        ref={ref}
        id={textId}
        size="sm"
        className="segmentText"
        {...(whole ? {} : { lineClamp: SEGMENT_LINES })}
      >
        {text}
      </Text>
      {(long || whole) && (
        <Button
          variant="subtle"
          size="compact-sm"
          px={6}
          aria-expanded={whole}
          aria-controls={textId}
          onClick={() => setWhole(!whole)}
        >
          {whole ? 'свернуть' : 'показать целиком'}
        </Button>
      )}
    </Stack>
  );
}

/** Описание сегмента: как есть — или правка. Пустое — предупреждение, а не тишина. */
function Segment({ home }: { home: SegmentHome }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (text: string) => writeSegment(home.hypothesis_id, text),
    onSuccess: () => {
      setDraft(null);
      void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
      void client.invalidateQueries({ queryKey: HYPOTHESES_QUERY_KEY });
    },
  });
  return (
    <Stack gap="xs">
      <Group justify="space-between" gap="xs">
        <Group gap={4} wrap="nowrap">
          <Title order={5}>Сегмент гипотезы</Title>
          <InfoHint name="Что видит судья сегмента" width={320}>
            Судья сравнивает с этим описанием текст сайта — выдачу и главную — и цитирует сайт
            дословно. Правка описания — новый вопрос: прежние вердикты по нему не годятся.
          </InfoHint>
        </Group>
        {draft === null && (
          <Button
            variant="default"
            size="compact-sm"
            className="press"
            onClick={() => setDraft(home.segment ?? '')}
          >
            {home.segment === null ? 'Описать сегмент' : 'Изменить'}
          </Button>
        )}
      </Group>
      {draft !== null ? (
        <Stack gap="xs">
          <Textarea
            aria-label="Описание сегмента"
            autosize
            minRows={3}
            maxRows={10}
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
          />
          <SegmentHint draft={draft} onDraft={setDraft} />
          <Group gap="sm">
            <Button className="press" loading={save.isPending} onClick={() => save.mutate(draft)}>
              Сохранить
            </Button>
            <Button
              variant="default"
              className="press"
              disabled={save.isPending}
              onClick={() => setDraft(null)}
            >
              Отмена
            </Button>
          </Group>
          {save.error !== null && (
            <Alert color="red" title="Описание не сохранено">
              {refusalOf(save.error)}
            </Alert>
          )}
        </Stack>
      ) : home.segment !== null ? (
        <SegmentText text={home.segment} />
      ) : (
        <Alert color="yellow" title="Описания сегмента нет">
          Судье не с чем сравнивать сайты — опишите, кому пишет гипотеза: какие это сайты и чем они
          живут.
        </Alert>
      )}
    </Stack>
  );
}

/** Прогон в списке: номер, когда, ключи, рынок — и под ним где он сейчас. */
function RunOption({ label, row }: { label: string; row: SegmentRunRow | undefined }) {
  return (
    <Stack gap={0}>
      <Text size="sm">{label}</Text>
      {row !== undefined && (
        <Text size="xs" c="dimmed">
          {runState(row)}
        </Text>
      )}
    </Stack>
  );
}

function Runs({
  home,
  chosen,
  onChoose,
}: {
  home: SegmentHome;
  chosen: number | null;
  onChoose: (run: number) => void;
}) {
  if (home.runs.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Прогонов у гипотезы пока нет — отчёт появится здесь после запуска.
      </Text>
    );
  }
  const run = chosen ?? home.runs[0]?.id ?? null;
  const rows = new Map(home.runs.map((row) => [String(row.id), row]));
  const data = home.runs.map((row) => ({ value: String(row.id), label: runTitle(row) }));
  // Прогон по ссылке — не из последних двадцати: его отчёт открывается всё равно.
  if (run !== null && !rows.has(String(run))) data.push({ value: String(run), label: `№${run}` });
  return (
    <Stack gap="sm">
      <Select
        label="Прогон"
        allowDeselect={false}
        data={data}
        value={run === null ? null : String(run)}
        onChange={(value) => {
          if (value !== null) onChoose(Number(value));
        }}
        renderOption={({ option }) => (
          <RunOption label={option.label} row={rows.get(option.value)} />
        )}
        comboboxProps={dropdownBelow}
        w={{ base: '100%', xs: 420 }}
      />
      {run !== null && <SegmentReport run={run} />}
    </Stack>
  );
}

function HypothesisSegment({
  hypothesis,
  run,
  onRun,
}: {
  hypothesis: number;
  run: number | null;
  onRun: (run: number) => void;
}) {
  const client = useQueryClient();
  const home = useSegment(hypothesis);
  if (home.data === undefined) {
    if (!home.error) return <Loader aria-label="Загружаем сбор базы" m="md" />;
    return (
      <Alert color="red" title="Сбор базы не загрузился">
        {refusalOf(home.error)}
      </Alert>
    );
  }
  return (
    <Stack gap="md">
      <Card component="section" className="glassQuiet" p="md" aria-label="Сегмент гипотезы">
        <Segment home={home.data} />
      </Card>
      <Card component="section" className="glassQuiet" p="md" aria-label="Новый прогон">
        <SegmentForm
          home={home.data}
          onStarted={(started) => {
            onRun(started);
            void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
          }}
        />
      </Card>
      <Card component="section" className="glassQuiet" p="md" aria-label="Прогоны гипотезы">
        <Runs home={home.data} chosen={run} onChoose={onRun} />
      </Card>
    </Stack>
  );
}

interface Props {
  hypotheses: HypothesisCard[];
  /** Гипотеза раздела из адреса; не выбрана — первая: прогон идёт по одной гипотезе. */
  hypothesis: number | null;
  onHypothesis: (hypothesis: number) => void;
  /** Прогон из адреса (`?run=N`); не выбран — последний прогон гипотезы. */
  run: number | null;
  onRun: (run: number) => void;
}

/** Выбор гипотезы вкладки — та же `?hypothesis=`, что у остальных вкладок раздела. */
function Pick({
  rows,
  chosen,
  onPick,
}: {
  rows: HypothesisCard[];
  chosen: number;
  onPick: (hypothesis: number) => void;
}) {
  const options = rows.map(({ id, name }) => ({ label: name, value: `${id}` }));
  return (
    <Select
      w={{ base: '100%', xs: 280 }}
      comboboxProps={dropdownBelow}
      label="Гипотеза"
      data={options}
      value={`${chosen}`}
      allowDeselect={false}
      onChange={(picked) => picked !== null && onPick(Number(picked))}
    />
  );
}

/** Вступление вкладки одной строкой, как идёт прогон — в «i». */
function Intro() {
  return (
    <Text size="sm" style={{ flex: '1 1 20rem', minWidth: 0 }}>
      Сбор новых сайтов по ключам: судья проверяет, подходит ли сайт под сегмент, а подходящим
      ищется адрес — и они становятся лидами гипотезы.{' '}
      <InfoHint name="Как идёт прогон" width={360}>
        Ключи → выдача → судья сегмента с цитатой сайта → адрес лестницей контактов → лиды и та же
        очистка, что у загрузки базы: дубли, стоп-лист, отписки и мёртвые адреса отсекаются до
        первого письма. Смета — до запуска.
      </InfoHint>
    </Text>
  );
}

export function SegmentPane({ hypotheses, hypothesis, onHypothesis, run, onRun }: Props) {
  const first = hypotheses.at(0);
  const chosen = hypothesis ?? (first === undefined ? null : first.id);
  return chosen === null ? (
    <Text px="md" size="sm" c="dimmed">
      Сбор базы идёт по гипотезе, а гипотез пока нет — заведите её на вкладке «Гипотезы».
    </Text>
  ) : (
    <Stack gap="md">
      <Group px="md" gap="md" wrap="wrap" align="flex-end" justify="space-between">
        <Intro />
        <Pick rows={hypotheses} chosen={chosen} onPick={onHypothesis} />
      </Group>
      <HypothesisSegment key={chosen} hypothesis={chosen} run={run} onRun={onRun} />
    </Stack>
  );
}
