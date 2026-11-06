/**
 * Окно сметы «Переобойти спорные». **Смета — всегда свежая** и открывается и при нуле: «к разбору 0,
 * уже разобраны N» отвечает на «почему не работает» лучше выключенной кнопки. Запуск — только по той
 * смете, что видел человек; отказ сервера — в окне, над кнопками.
 */

import { Alert, List, Loader, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import {
  fetchJudgePass,
  JUDGE_PASS_KEY,
  passMoving,
  passResumes,
  startJudgePass,
} from '../api/judgePass';
import type { JudgePassView } from '../api/judgePass';
import { SaveActions } from '../components/SaveActions';
import { formatNumber } from '../format';
import { notify } from '../notices';

const INTRO =
  'Судья заново откроет страницу со ссылкой и сайт рекламодателя и поставит совет. Решения людей, ' +
  'очередь и письма не меняются: совет виден только под решением.';

/** Смета в окне: что считается, почему не посчиталась, строки сервера и его отказ. */
function Estimate({ view, error }: { view: JudgePassView | undefined; error: Error | null }) {
  if (error !== null) {
    return (
      <Alert color="red" title="Смета не посчиталась">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (view === undefined) return <Loader aria-label="Считаем смету разбора" size="sm" />;
  return (
    <>
      <List size="sm" spacing={4} aria-label="Смета разбора">
        {view.estimate.lines.map((line) => (
          <List.Item key={line}>{line}</List.Item>
        ))}
      </List>
      {view.estimate.refusal !== null && (
        <Alert color="red" title="Разбор не запустить">
          {view.estimate.refusal}
        </Alert>
      )}
    </>
  );
}

/** Почему запускать нечего — строкой под кнопками; отказ сервера назван выше, в смете. */
function lockedWords(view: JudgePassView | undefined): string | null {
  if (passMoving(view)) return 'Разбор уже идёт.';
  return view?.estimate.cases === 0 ? 'Спорных к разбору нет.' : null;
}

export function JudgePassModal({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  // Смета — своим ключом и свежая на каждое открытие: могли прийти новые спорные и выбрать потолок.
  const found = useQuery({
    queryKey: [...JUDGE_PASS_KEY, 'estimate'],
    queryFn: fetchJudgePass,
    gcTime: 0,
  });
  const start = useMutation({
    mutationFn: startJudgePass,
    onSuccess: (view) => {
      client.setQueryData(JUDGE_PASS_KEY, view);
      notify({ message: 'Разбор спорных поставлен', color: 'green' });
      onClose();
    },
  });
  const view = found.data;
  const cases = view?.estimate.cases ?? 0;
  return (
    <Modal opened onClose={onClose} title="Переобойти спорные" size="lg">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          start.mutate(cases);
        }}
      >
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            {INTRO}
          </Text>
          {start.error !== null && (
            <Alert color="red" title="Разбор не запущен">
              {refusalOf(start.error)}
            </Alert>
          )}
          <Estimate view={view} error={found.error} />
          <SaveActions
            onCancel={onClose}
            saving={start.isPending}
            locked={lockedWords(view)}
            disabled={view === undefined || view.estimate.refusal !== null}
            saveLabel={`${passResumes(view) ? 'Продолжить' : 'Переобойти'} · ${formatNumber(cases)}`}
          />
        </Stack>
      </form>
    </Modal>
  );
}
