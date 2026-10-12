/**
 * Новый прогон гипотезы: ключи, рынок выдачи, язык писем и глубина — смета до запуска.
 *
 * **Смета — до кнопки «Запустить», и считает её сервер** (`POST /sales/segment/estimate`):
 * выдача, модель и Hunter строкой, отсеянные ключи с причиной и отказ словами — выше
 * остатка дневного потолка модели, нет описания сегмента, песочница выдачи. Запуск
 * открыт только по смете тех же значений формы: правка поля снимает смету, и запустить
 * то, чего не видел, нельзя. Сервер на запуске считает её заново (`POST /sales/segment/runs`)
 * и отказывает теми же словами.
 *
 * **Право — «run»**: выдача, модель и платный поиск адресов — траты, как прогон доноров и
 * очистка лидов. Без права смету видно, а у кнопки — кто запускает.
 */

import { Alert, Button, Group, List, Select, Stack, Text, Textarea, Title } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { countryTitle, PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { notify } from '../notices';
import { dropdownBelow } from '../theme';
import { estimateRun, startRun } from './segmentApi';
import type { SegmentAsk, SegmentEstimate, SegmentHome } from './segmentApi';
import { DEPTHS, depthTitle, LANGUAGE_TITLES } from './segmentData';

/** Рынок, пока человек не выбрал: англоязычная выдача — гипотеза 2. */
const DEFAULT_COUNTRY = 'us';
/** Язык писем по рынку — пустым значением: его называет сервер в смете. */
const BY_MARKET = '';

interface Draft {
  keywords: string;
  country: string;
  language: string;
  depth: number;
}

function askOf(hypothesis: number, draft: Draft): SegmentAsk {
  return {
    hypothesis_id: hypothesis,
    keywords: draft.keywords,
    country: draft.country,
    language: draft.language === BY_MARKET ? null : draft.language,
    depth_pages: draft.depth,
  };
}

function Rejected({ rejected }: { rejected: Record<string, string> }) {
  const rows = Object.entries(rejected);
  if (rows.length === 0) return null;
  return (
    <Stack gap={2}>
      <Text size="sm">Отсеяно ключей: {formatNumber(rows.length)}</Text>
      <List size="sm" spacing={2} aria-label="Отсеянные ключи">
        {rows.map(([phrase, why]) => (
          <List.Item key={phrase}>
            «{phrase}» — {why}
          </List.Item>
        ))}
      </List>
    </Stack>
  );
}

/** Смета словами сервера и отказ, если запускать нельзя. */
function EstimateBlock({ found }: { found: SegmentEstimate }) {
  return (
    <Stack gap={6} aria-label="Смета прогона">
      {found.lines.length > 0 && (
        <Stack gap={2} className="segmentEstimate">
          {found.lines.map((line) => (
            <Text key={line} size="sm">
              {line}
            </Text>
          ))}
        </Stack>
      )}
      <Rejected rejected={found.rejected} />
      {found.refusal !== null && (
        <Alert color="red" title="Прогон не запустится">
          {found.refusal}
        </Alert>
      )}
    </Stack>
  );
}

function Fields({
  home,
  draft,
  onDraft,
}: {
  home: SegmentHome;
  draft: Draft;
  onDraft: (patch: Partial<Draft>) => void;
}) {
  return (
    <>
      <Textarea
        label="Ключи"
        description={`По ключу на строку, не больше ${formatNumber(home.max_keywords)}: гигиена и почти-повторы отсеиваются до выдачи.`}
        autosize
        minRows={4}
        maxRows={12}
        value={draft.keywords}
        onChange={(event) => onDraft({ keywords: event.currentTarget.value })}
      />
      <Group gap="md" align="flex-start" wrap="wrap">
        <Select
          label="Рынок выдачи"
          searchable
          allowDeselect={false}
          data={home.countries.map((code) => ({ value: code, label: countryTitle(code) }))}
          value={draft.country}
          onChange={(value) => onDraft({ country: value ?? DEFAULT_COUNTRY })}
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 220 }}
        />
        <Select
          label="Язык писем"
          allowDeselect={false}
          data={[
            { value: BY_MARKET, label: 'язык рынка' },
            ...home.languages.map((code) => ({
              value: code,
              label: LANGUAGE_TITLES[code] ?? code,
            })),
          ]}
          value={draft.language}
          onChange={(value) => onDraft({ language: value ?? BY_MARKET })}
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 180 }}
        />
        <Select
          label="Глубина выдачи"
          allowDeselect={false}
          data={DEPTHS.filter((pages) => pages <= home.max_depth).map((pages) => ({
            value: String(pages),
            label: depthTitle(pages),
          }))}
          value={String(draft.depth)}
          onChange={(value) => onDraft({ depth: Number(value ?? 1) })}
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 240 }}
        />
      </Group>
    </>
  );
}

interface Props {
  home: SegmentHome;
  onStarted: (run: number) => void;
}

export function SegmentForm({ home, onStarted }: Props) {
  const { can } = useSession();
  const [draft, setDraft] = useState<Draft>({
    keywords: '',
    country: DEFAULT_COUNTRY,
    language: BY_MARKET,
    depth: 1,
  });
  const ask = askOf(home.hypothesis_id, draft);
  const asked = JSON.stringify(ask);
  // Смета — для тех значений формы, по которым её считали: правка поля её снимает.
  const [counted, setCounted] = useState<{ key: string; found: SegmentEstimate } | null>(null);
  const found = counted !== null && counted.key === asked ? counted.found : null;
  const count = useMutation({
    mutationFn: (sent: SegmentAsk) => estimateRun(sent),
    onSuccess: (estimate, sent) => setCounted({ key: JSON.stringify(sent), found: estimate }),
  });
  const start = useMutation({
    mutationFn: (sent: SegmentAsk) => startRun(sent),
    onSuccess: (queued) => {
      notify({ message: `Прогон №${queued.run_id} ушёл в очередь задач`, color: 'green' });
      setCounted(null);
      onStarted(queued.run_id);
    },
  });
  const ready = found !== null && found.refusal === null;
  return (
    <Stack gap="sm">
      <Title order={5}>Новый прогон</Title>
      <Fields
        home={home}
        draft={draft}
        onDraft={(patch) => {
          start.reset();
          setDraft((current) => ({ ...current, ...patch }));
        }}
      />
      <Group gap="sm" wrap="wrap">
        <Button
          variant="default"
          className="press"
          loading={count.isPending}
          disabled={draft.keywords.trim() === ''}
          onClick={() => count.mutate(ask)}
        >
          Посчитать смету
        </Button>
        {can('run') ? (
          <Button
            className="press"
            disabled={!ready}
            loading={start.isPending}
            onClick={() => start.mutate(ask)}
          >
            Запустить прогон
          </Button>
        ) : (
          <Text size="sm" c="dimmed">
            Запускает сотрудник с правом «{PERMISSION_TITLES.run}».
          </Text>
        )}
      </Group>
      {count.error !== null && (
        <Alert color="red" title="Смета не посчитана">
          {refusalOf(count.error)}
        </Alert>
      )}
      {found !== null && <EstimateBlock found={found} />}
      {start.error !== null && (
        <Alert color="red" title="Прогон не запущен">
          {refusalOf(start.error)}
        </Alert>
      )}
    </Stack>
  );
}
