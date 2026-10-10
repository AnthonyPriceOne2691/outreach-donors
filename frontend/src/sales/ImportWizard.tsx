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
 *
 * **Гипотезы нет — она заводится здесь же**, окном «Новая гипотеза», и заведённая
 * сразу выбрана; после загрузки здесь же запускается очистка (`CleanLeads`) — мастер
 * не отправляет человека в консоль ни до, ни после.
 */

import {
  Alert,
  Button,
  Card,
  FileInput,
  Group,
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
import type { HypothesesView, HypothesisCard, IntakeView, LeadField } from '../api/salesTypes';
import { BackLink } from '../components/BackLink';
import { ImportColumns } from './ImportColumns';
import { withField } from './importMapping';
import type { FieldMapping } from './importMapping';
import { ImportOutcome, ImportReport } from './ImportReport';
import { Pending } from './Pending';
import { HYPOTHESES_QUERY_KEY } from './hypothesisData';
import { NewHypothesisButton } from './HypothesisModal';
import { ModuleOff } from './ModuleOff';

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
  if (props.hypothesisId === null) {
    const pick = props.hypotheses.length === 0 ? 'Заведите' : 'Выберите';
    return `${pick} гипотезу — куда лягут лиды.`;
  }
  if (props.kind === 'file' && props.file === null) return 'Выберите файл.';
  if (props.kind === 'link' && props.link.trim() === '') return 'Вставьте ссылку на таблицу.';
  return null;
}

/** Гипотеза, куда лягут лиды: выбрать из заведённых или завести здесь же — заведённая
 *  окном сразу выбрана. Нет ни одной — плашка зовёт к той же кнопке, а не к консоли. */
function HypothesisPick({ hypotheses, hypothesisId, onHypothesis }: SourceStepProps) {
  return (
    <Stack gap="xs">
      {hypotheses.length === 0 ? (
        <Alert color="yellow" title="Гипотез пока нет">
          Гипотеза — кому и зачем пишем; базу грузят в неё. Заведите её здесь — кнопкой «Новая
          гипотеза».
        </Alert>
      ) : null}
      <Group align="flex-end" gap="sm" wrap="wrap">
        {hypotheses.length > 0 ? (
          <Select
            label="Гипотеза"
            description="Куда лягут лиды."
            placeholder="выберите"
            data={hypotheses.map((row) => ({ value: String(row.id), label: row.name }))}
            value={hypothesisId === null ? null : String(hypothesisId)}
            onChange={(value) => onHypothesis(value === null ? null : Number(value))}
            allowDeselect={false}
            style={{ flex: '1 1 16rem' }}
          />
        ) : null}
        <NewHypothesisButton onAdded={(card) => onHypothesis(card.id)} />
      </Group>
    </Stack>
  );
}

function SourceStep(props: SourceStepProps) {
  const { kind, file, link, busy, refusal } = props;
  const missing = missingOf(props);
  return (
    <Stack gap="md" maw={560}>
      <HypothesisPick {...props} />
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

/** Имя гипотезы по номеру — для слов «лиды лягут в гипотезу …». */
function nameOf(known: HypothesisCard[], id: number | null): string | null {
  return known.find((row) => row.id === id)?.name ?? null;
}

/**
 * Состояние мастера: выбор человека и последние ответы сервера. Сервер между
 * шагами ничего не помнит, поэтому всё, что уходит на предпросмотр и загрузку,
 * держится здесь — и здесь же правила переходов между шагами.
 */
function useImportWizard() {
  const queryClient = useQueryClient();
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

  /** Смена источника обнуляет прочитанное: иначе новый файл ушёл бы со старыми колонками. */
  function changing<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      forget();
    };
  }

  return {
    step,
    setStep,
    hypothesisId,
    setHypothesisId,
    kind,
    file,
    link,
    found,
    mapping,
    header,
    outcome,
    preview,
    load,
    /** Отказы сервера словами — там, где их чинят. */
    previewRefusal: preview.error ? refusalOf(preview.error) : null,
    loadRefusal: load.error ? refusalOf(load.error) : null,
    changeKind: changing(setKind),
    changeFile: changing(setFile),
    changeLink: changing(setLink),
    read: () => {
      setOutcome(null);
      preview.mutate({});
    },
    remap: (column: number, field: LeadField | null) => {
      const next = withField(mapping, column, field);
      setMapping(next);
      preview.mutate({ mapping: next, header });
    },
    reheader: (checked: boolean) => {
      setHeader(checked);
      preview.mutate({ mapping, header: checked });
    },
    again: () => {
      forget();
      setFile(null);
      setLink('');
      setOutcome(null);
      preview.reset();
      load.reset();
    },
  };
}

type Wizard = ReturnType<typeof useImportWizard>;

/** Шаги мастера и тело текущего: источник, колонки, отчёт. */
function WizardSteps({ wizard, known }: { wizard: Wizard; known: HypothesisCard[] }) {
  const { step, found, hypothesisId, preview, load } = wizard;
  return (
    <Stack gap="lg">
      <Stepper
        active={step}
        size="sm"
        allowNextStepsSelect={false}
        onStepClick={(to) => wizard.setStep(to === 1 || to === 2 ? to : 0)}
      >
        {STEPS.map((named) => (
          <Stepper.Step key={named.label} label={named.label} description={named.description} />
        ))}
      </Stepper>

      {step === 0 && (
        <SourceStep
          hypotheses={known}
          hypothesisId={hypothesisId}
          kind={wizard.kind}
          file={wizard.file}
          link={wizard.link}
          busy={preview.isPending}
          refusal={wizard.previewRefusal}
          onHypothesis={wizard.setHypothesisId}
          onKind={wizard.changeKind}
          onFile={wizard.changeFile}
          onLink={wizard.changeLink}
          onRead={wizard.read}
        />
      )}

      {step === 1 && found !== null && (
        <ImportColumns
          found={found}
          mapping={wizard.mapping}
          header={wizard.header}
          busy={preview.isPending}
          refusal={wizard.previewRefusal}
          onField={wizard.remap}
          onHeader={wizard.reheader}
          onBack={() => wizard.setStep(0)}
          onNext={() => wizard.setStep(2)}
        />
      )}

      {step === 2 && found !== null && hypothesisId !== null && (
        <ImportReport
          found={found}
          hypothesisName={nameOf(known, hypothesisId)}
          busy={load.isPending}
          refusal={wizard.loadRefusal}
          onBack={() => wizard.setStep(1)}
          onLoad={() => load.mutate(hypothesisId)}
        />
      )}
    </Stack>
  );
}

/** Шапка мастера: куда вернуться и что будет до записи. Модуль выключен — строкой под ней,
 *  как под шапкой раздела: база грузится, а письма из неё не уйдут. */
function WizardHead({ view }: { view: HypothesesView }) {
  return (
    <Card className="glassPanel" p="xl">
      <Stack gap={6}>
        <BackLink to="/sales">К продажам</BackLink>
        <Title order={3}>Загрузка базы</Title>
        <Text size="sm" c="dimmed" maw={720}>
          Файл CSV или Google-таблица → колонки → отчёт по каждой строке → запись в гипотезу. До
          последнего шага в базу ничего не пишется: сначала мастер показывает, что получится.
        </Text>
        <ModuleOff view={view} />
      </Stack>
    </Card>
  );
}

export function ImportWizard() {
  const hypotheses = useQuery({ queryKey: HYPOTHESES_QUERY_KEY, queryFn: listHypotheses });
  const wizard = useImportWizard();

  if (hypotheses.data === undefined) return <Pending error={hypotheses.error} />;

  const known = hypotheses.data.rows;
  const { outcome, hypothesisId } = wizard;
  return (
    <Stack gap="lg">
      <WizardHead view={hypotheses.data} />

      <Card className="glassPanel" p="xl">
        {outcome !== null && hypothesisId !== null ? (
          <ImportOutcome
            outcome={outcome}
            hypothesisId={hypothesisId}
            hypothesisName={nameOf(known, hypothesisId)}
            waiting={known.find((row) => row.id === hypothesisId)?.leads.new ?? 0}
            onAgain={wizard.again}
          />
        ) : (
          <WizardSteps wizard={wizard} known={known} />
        )}
      </Card>
    </Stack>
  );
}
