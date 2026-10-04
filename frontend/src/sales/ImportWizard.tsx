/**
 * Мастер загрузки базы лидов: источник → колонки → отчёт → загрузка.
 *
 * **Сервер между шагами ничего не помнит** (контракт 1.3c): источник — файл
 * или ссылка — уходит и на каждый предпросмотр, и на загрузку; таблица по
 * ссылке читается заново. Мастер держит у себя только выбор человека:
 * гипотезу, источник, сопоставление колонок и признак заголовка.
 *
 * **Предпросмотр — настоящий сухой прогон**, не догадка экрана: что станет
 * лидом и что нет, решает тот же код, что пишет в базу, поэтому «станут
 * лидами 97» до загрузки и «загружено 97» после — одно число (A1).
 *
 * **Отказ сервера показан словами там, где его чинят**: ссылка не открылась —
 * на шаге источника, колонки почты нет — на шаге колонок. Загрузка невозможна,
 * пока предпросмотр не показал, что получится: смена файла или ссылки
 * обнуляет прочитанное — иначе новый файл ушёл бы со старыми колонками.
 *
 * **Правка колонок — пересчёт, и экран ждёт только последний ответ.** Два
 * быстрых переключения — два запроса; пришедший первым ответ на устаревший
 * запрос не перебивает выбор человека.
 */

import {
  Alert,
  Button,
  Card,
  FileInput,
  Group,
  Loader,
  Radio,
  Select,
  Stack,
  Stepper,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconUpload } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import { listHypotheses, loadImport, previewImport } from '../api/sales';
import type { ImportOptions, ImportSource } from '../api/sales';
import type { HypothesisCard, IntakeView, LeadField } from '../api/salesTypes';
import { BackLink } from '../components/BackLink';
import { ImportColumns } from './ImportColumns';
import { withField } from './importMapping';
import type { FieldMapping } from './importMapping';
import { ImportOutcome, ImportReport } from './ImportReport';
import { HYPOTHESES_QUERY_KEY } from './SalesPage';

/** Шаги мастера по порядку. Итог загрузки — не шаг: назад с него не ходят. */
const STEPS = [
  { label: 'Источник', description: 'файл или ссылка' },
  { label: 'Колонки', description: 'что куда ложится' },
  { label: 'Отчёт', description: 'что получится' },
] as const;

type Step = 0 | 1 | 2;
type SourceKind = 'file' | 'link';

/** Ключ всего раздела: после загрузки устаревают и счётчики гипотез, и лиды. */
const SALES_QUERIES = ['sales'] as const;

interface SourceStepProps {
  hypotheses: HypothesisCard[];
  hypothesisId: number | null;
  kind: SourceKind;
  file: File | null;
  link: string;
  busy: boolean;
  refusal: string | null;
  onHypothesis: (id: number | null) => void;
  onKind: (kind: SourceKind) => void;
  onFile: (file: File | null) => void;
  onLink: (link: string) => void;
  onRead: () => void;
}

/** Чего не хватает, чтобы прочитать источник, — словами у кнопки. */
function missingOf(props: SourceStepProps): string | null {
  if (props.hypothesisId === null) return 'Выберите гипотезу — куда лягут лиды.';
  if (props.kind === 'file' && props.file === null) return 'Выберите файл.';
  if (props.kind === 'link' && props.link.trim() === '') return 'Вставьте ссылку на таблицу.';
  return null;
}

function SourceStep(props: SourceStepProps) {
  const { hypotheses, hypothesisId, kind, file, link, busy, refusal } = props;
  const missing = missingOf(props);
  return (
    <Stack gap="md" maw={560}>
      {hypotheses.length === 0 ? (
        <Alert color="yellow" title="Гипотез пока нет">
          Гипотеза — кому и зачем пишем; базу грузят в неё. Заведите командой{' '}
          <code>outreach sales-hypothesis-add</code> и возвращайтесь.
        </Alert>
      ) : (
        <Select
          label="Гипотеза"
          description="Куда лягут лиды. Гипотеза заводится командой outreach sales-hypothesis-add."
          placeholder="выберите"
          data={hypotheses.map((row) => ({ value: String(row.id), label: row.name }))}
          value={hypothesisId === null ? null : String(hypothesisId)}
          onChange={(value) => props.onHypothesis(value === null ? null : Number(value))}
          allowDeselect={false}
        />
      )}
      <Radio.Group
        label="Откуда база"
        value={kind}
        onChange={(value) => props.onKind(value === 'link' ? 'link' : 'file')}
      >
        <Group mt="xs" gap="lg">
          <Radio value="file" label="Файл CSV" />
          <Radio value="link" label="Ссылка на Google-таблицу" />
        </Group>
      </Radio.Group>
      {kind === 'file' ? (
        <FileInput
          label="Файл с базой"
          description="CSV до 10 МБ в UTF-8; разделитель — запятая, точка с запятой или табуляция, угадывается."
          placeholder="выберите файл"
          accept=".csv,text/csv,text/plain"
          leftSection={<IconUpload size={16} />}
          value={file}
          onChange={props.onFile}
          clearable
        />
      ) : (
        <TextInput
          label="Ссылка на Google-таблицу"
          description="Таблица должна открываться по ссылке: «Доступ всем, у кого есть ссылка»."
          placeholder="https://docs.google.com/spreadsheets/d/…"
          value={link}
          onChange={(event) => props.onLink(event.currentTarget.value)}
        />
      )}
      {refusal !== null && (
        <Alert color="red" title="Источник не прочитан">
          {refusal}
        </Alert>
      )}
      <Group gap="md">
        <Button className="press" loading={busy} disabled={missing !== null} onClick={props.onRead}>
          Прочитать
        </Button>
        {missing !== null && (
          <Text size="sm" c="dimmed">
            {missing}
          </Text>
        )}
      </Group>
    </Stack>
  );
}

export function ImportWizard() {
  const queryClient = useQueryClient();
  const hypotheses = useQuery({ queryKey: HYPOTHESES_QUERY_KEY, queryFn: listHypotheses });

  const [step, setStep] = useState<Step>(0);
  const [hypothesisId, setHypothesisId] = useState<number | null>(null);
  const [kind, setKind] = useState<SourceKind>('file');
  const [file, setFile] = useState<File | null>(null);
  const [link, setLink] = useState('');
  // Последний предпросмотр и то, что человек поправил поверх него: правка
  // показывается сразу, а ответ сервера подтверждает её тем же сопоставлением.
  const [found, setFound] = useState<IntakeView | null>(null);
  const [mapping, setMapping] = useState<FieldMapping>({});
  const [header, setHeader] = useState(false);
  const [outcome, setOutcome] = useState<IntakeView | null>(null);
  // Номер последнего запроса предпросмотра: ответы на прежние — не в счёт.
  const requests = useRef(0);

  const source: ImportSource = kind === 'file' ? { file, link: '' } : { file: null, link };

  const preview = useMutation({
    mutationFn: async (options: ImportOptions) => {
      const sequence = ++requests.current;
      return { sequence, view: await previewImport(source, options) };
    },
    onSuccess: ({ sequence, view }) => {
      if (sequence !== requests.current) return;
      setFound(view);
      setMapping(view.mapping);
      setHeader(view.header);
      setStep((current) => (current === 0 ? 1 : current));
    },
  });

  const load = useMutation({
    mutationFn: (hypothesis: number) => loadImport(source, { mapping, header }, hypothesis),
    onSuccess: async (view) => {
      setOutcome(view);
      await queryClient.invalidateQueries({ queryKey: SALES_QUERIES });
    },
  });

  /** Источник сменился — прочитанное больше не про него. */
  const forget = () => {
    setFound(null);
    setStep(0);
  };

  const again = () => {
    forget();
    setFile(null);
    setLink('');
    setOutcome(null);
    preview.reset();
    load.reset();
  };

  const remap = (column: number, field: LeadField | null) => {
    const next = withField(mapping, column, field);
    setMapping(next);
    preview.mutate({ mapping: next, header });
  };

  const reheader = (checked: boolean) => {
    setHeader(checked);
    preview.mutate({ mapping, header: checked });
  };

  if (hypotheses.data === undefined) {
    return hypotheses.error ? (
      <Alert color="red" title="Раздел продаж не загрузился" m="md">
        {refusalOf(hypotheses.error)}
      </Alert>
    ) : (
      <Loader aria-label="Загружаем раздел продаж" m="md" />
    );
  }

  const known = hypotheses.data.rows;
  const hypothesisName = known.find((row) => row.id === hypothesisId)?.name ?? null;
  const refusal = preview.error ? refusalOf(preview.error) : null;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap={6}>
          <BackLink to="/sales">К продажам</BackLink>
          <Title order={3}>Загрузка базы</Title>
          <Text size="sm" c="dimmed" maw={720}>
            Файл CSV или Google-таблица → колонки → отчёт по каждой строке → запись в гипотезу. До
            последнего шага в базу ничего не пишется: сначала мастер показывает, что получится.
          </Text>
        </Stack>
      </Card>

      <Card className="glassPanel" p="xl">
        {outcome !== null && hypothesisId !== null ? (
          <ImportOutcome
            outcome={outcome}
            hypothesisId={hypothesisId}
            hypothesisName={hypothesisName}
            onAgain={again}
          />
        ) : (
          <Stack gap="lg">
            <Stepper
              active={step}
              size="sm"
              allowNextStepsSelect={false}
              onStepClick={(to) => setStep(to === 1 || to === 2 ? to : 0)}
            >
              {STEPS.map((named) => (
                <Stepper.Step
                  key={named.label}
                  label={named.label}
                  description={named.description}
                />
              ))}
            </Stepper>

            {step === 0 && (
              <SourceStep
                hypotheses={known}
                hypothesisId={hypothesisId}
                kind={kind}
                file={file}
                link={link}
                busy={preview.isPending}
                refusal={refusal}
                onHypothesis={setHypothesisId}
                onKind={(next) => {
                  setKind(next);
                  forget();
                }}
                onFile={(next) => {
                  setFile(next);
                  forget();
                }}
                onLink={(next) => {
                  setLink(next);
                  forget();
                }}
                onRead={() => {
                  setOutcome(null);
                  preview.mutate({});
                }}
              />
            )}

            {step === 1 && found !== null && (
              <ImportColumns
                found={found}
                mapping={mapping}
                header={header}
                busy={preview.isPending}
                refusal={refusal}
                onField={remap}
                onHeader={reheader}
                onBack={() => setStep(0)}
                onNext={() => setStep(2)}
              />
            )}

            {step === 2 && found !== null && hypothesisId !== null && (
              <ImportReport
                found={found}
                hypothesisName={hypothesisName}
                busy={load.isPending}
                refusal={load.error ? refusalOf(load.error) : null}
                onBack={() => setStep(1)}
                onLoad={() => load.mutate(hypothesisId)}
              />
            )}
          </Stack>
        )}
      </Card>
    </Stack>
  );
}
