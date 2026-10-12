/**
 * Вкладка «Сбор базы» — прогон гипотезы: ключи → выдача → судья сегмента → адрес → лиды.
 *
 * ТЗ, «Что получаем», п. 1: сбор новых сайтов по ключевым словам, ИИ проверяет, подходит ли
 * сайт под сегмент, дубли, стоп-лист и мёртвые адреса отсекаются до первого письма. Здесь:
 *
 * - **описание сегмента гипотезы** — его читает судья; правка — новый вопрос модели, прежние
 *   вердикты по нему не годятся. Без описания прогон не запускается (судье не с чем сравнивать);
 * - **новый прогон** — смета до запуска словами сервера (`SegmentForm`);
 * - **прогоны гипотезы** — последний сразу, прежние — списком; отчёт и решение человека по
 *   «на проверку» (`SegmentReport`).
 *
 * Гипотеза — та же, что у остальных вкладок раздела (`?hypothesis=`).
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
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { HypothesisCard } from '../api/salesTypes';
import { InfoHint } from '../components/InfoHint';
import { dropdownBelow } from '../theme';
import { HYPOTHESES_QUERY_KEY } from './hypothesisData';
import { writeSegment } from './segmentApi';
import type { SegmentHome } from './segmentApi';
import { runTitle, SEGMENT_QUERY_KEY, useSegment } from './segmentData';
import { SegmentForm } from './SegmentForm';
import { SegmentReport } from './SegmentReport';

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
        <Text size="sm" className="segmentText">
          {home.segment}
        </Text>
      ) : (
        <Alert color="yellow" title="Описания сегмента нет">
          Судье не с чем сравнивать сайты — опишите, кому пишет гипотеза: какие это сайты и чем они
          живут.
        </Alert>
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
  return (
    <Stack gap="sm">
      <Select
        label="Прогон"
        allowDeselect={false}
        data={home.runs.map((row) => ({ value: String(row.id), label: runTitle(row) }))}
        value={run === null ? null : String(run)}
        onChange={(value) => {
          if (value !== null) onChoose(Number(value));
        }}
        comboboxProps={dropdownBelow}
        w={{ base: '100%', xs: 420 }}
      />
      {run !== null && <SegmentReport run={run} />}
    </Stack>
  );
}

function HypothesisSegment({ hypothesis }: { hypothesis: number }) {
  const client = useQueryClient();
  const [chosen, setChosen] = useState<number | null>(null);
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
          onStarted={(run) => {
            setChosen(run);
            void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
          }}
        />
      </Card>
      <Card component="section" className="glassQuiet" p="md" aria-label="Прогоны гипотезы">
        <Runs home={home.data} chosen={chosen} onChoose={setChosen} />
      </Card>
    </Stack>
  );
}

interface Props {
  hypotheses: HypothesisCard[];
  /** Гипотеза раздела из адреса; не выбрана — первая: прогон идёт по одной гипотезе. */
  hypothesis: number | null;
  onHypothesis: (hypothesis: number) => void;
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

export function SegmentPane({ hypotheses, hypothesis, onHypothesis }: Props) {
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
      <HypothesisSegment key={chosen} hypothesis={chosen} />
    </Stack>
  );
}
