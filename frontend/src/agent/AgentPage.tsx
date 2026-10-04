/**
 * Агент переписки: по каким настройкам он пишет собеседнику.
 *
 * Решение Anthony 04.10.2026: агент готовит черновики ответов, человек
 * правит и отправляет. Настройки — у каждого этапа свои: донорам мы
 * покупаем размещение и торгуемся вниз, рекламодателям продаём и держим
 * цену.
 *
 * **Сохранение заводит новую версию, а не правит старую** — как у порогов:
 * черновик агента объясняется той версией, по которой написан. Пока версий
 * у этапа нет, агент на нём не пишет, и экран говорит это словами, а
 * умолчания показывает как отправную точку.
 */

import { Alert, Badge, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { fetchAgentSettings } from '../api/agent';
import type { AgentStageView } from '../api/agent';
import { refusalOf } from '../api/client';
import type { LetterStage } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { StageSwitch } from '../components/StageSwitch';
import { formatDateTime } from '../format';
import { AgentForm } from './AgentForm';
import { AgentHistory } from './AgentHistory';
import { bodyOf, draftOf, refusalsOf, sameDraft } from './agentDraft';
import type { AgentDraft } from './agentDraft';
import { AGENT_KEY, useAgentSave } from './useAgentSave';

/** Что агент делает на этапе и как читается предел цены. */
const ABOUT: Record<LetterStage, { lead: string; price: string; limit: string }> = {
  donors: {
    lead: 'Донорам мы покупаем размещение: агент узнаёт цену и условия и торгуется вниз.',
    price: 'Не дороже, $',
    limit: 'не дороже',
  },
  advertisers: {
    lead: 'Рекламодателям мы продаём размещение: агент отвечает на вопросы и держит цену.',
    price: 'Не дешевле, $',
    limit: 'не дешевле',
  },
};

type Drafts = Partial<Record<LetterStage, AgentDraft>>;

function without(drafts: Drafts, stage: LetterStage): Drafts {
  const rest = { ...drafts };
  delete rest[stage];
  return rest;
}

/** Действует ли агент на этапе — значком и словами. */
function Standing({ view }: { view: AgentStageView }) {
  if (view.current === null) {
    return (
      <Text size="sm" className="agentStanding">
        Этап не настроен — агент на нём не пишет. Ниже — с чего начать: сохраните, и он начнёт
        готовить черновики.
      </Text>
    );
  }
  const { version, created_by: author, created_at: at, settings } = view.current;
  const who = author === null ? formatDateTime(at) : `${author} · ${formatDateTime(at)}`;
  return (
    <Group gap="xs">
      <Badge variant="light" color={settings.enabled ? 'green' : 'yellow'}>
        {settings.enabled ? 'пишет черновики' : 'выключен'}
      </Badge>
      <Text size="sm" className="agentStanding">
        Действует версия {version} — {who}.
      </Text>
    </Group>
  );
}

interface StageProps {
  view: AgentStageView;
  canEdit: boolean;
  /** Набранное на этапе; нет — экран показывает действующие. */
  edit: AgentDraft | undefined;
  onEdit: (draft: AgentDraft | null) => void;
  /** Сохранён этап — его набранное снимается. Этап приходит из сохранения,
   *  а не из экрана: пока шёл запрос, могли переключиться на другой. */
  onSaved: (stage: LetterStage) => void;
}

/** Настройки одного этапа: что действует и поля правки. */
function StageSettings({ view, canEdit, edit, onEdit, onSaved }: StageProps) {
  const save = useAgentSave(onSaved);
  const inUse = view.current?.settings ?? view.defaults;
  const draft = edit ?? draftOf(inUse);
  const body = bodyOf(draft);
  const edited = !sameDraft(draft, inUse);
  // Не настроенный этап сохраняют и без правки: умолчания становятся
  // первой версией, и с неё агент начинает писать.
  const nothingToSave = view.current !== null && !edited;
  return (
    <>
      <Standing view={view} />
      <AgentForm
        draft={draft}
        refusals={canEdit ? refusalsOf(draft) : {}}
        canEdit={canEdit}
        priceLabel={ABOUT[view.stage].price}
        onEdit={(change) => onEdit({ ...draft, ...change })}
        save={{
          busy: save.isPending,
          disabled: !canEdit || body === null || nothingToSave,
          onSave: () => body !== null && save.mutate({ stage: view.stage, body }),
        }}
        onRevert={edited ? () => onEdit(null) : null}
      />
    </>
  );
}

export function AgentPage() {
  const { can } = useSession();
  const [stage, setStage] = useState<LetterStage>('donors');
  // Черновик у каждого этапа свой: переключение этапа не теряет набранное.
  const [drafts, setDrafts] = useState<Drafts>({});
  const { data, error } = useQuery({ queryKey: AGENT_KEY, queryFn: fetchAgentSettings });
  const view = data?.stages.find((one) => one.stage === stage);

  let content = <Loader size="sm" aria-label="Загружаем настройки агента" />;
  if (error) {
    content = (
      <Alert color="red" title="Настройки агента не загрузились">
        {refusalOf(error)}
      </Alert>
    );
  } else if (view !== undefined) {
    content = (
      <StageSettings
        key={stage}
        view={view}
        canEdit={can('settings')}
        edit={drafts[stage]}
        onEdit={(draft) =>
          setDrafts((all) => (draft === null ? without(all, stage) : { ...all, [stage]: draft }))
        }
        onSaved={(saved) => setDrafts((all) => without(all, saved))}
      />
    );
  }

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          {/* Заголовок и переключатель стоят при любой загрузке и любом
              отказе, как у писем. */}
          <Title order={3}>Агент переписки</Title>
          <Text size="sm" c="dimmed" maw={680}>
            Агент готовит черновик ответа собеседнику по этим настройкам, человек правит его и
            отправляет. Сохранение заводит новую версию: черновик объясняется той, по которой
            написан.
          </Text>
          <StageSwitch label="Этап" value={stage} onChange={setStage} lead={ABOUT[stage].lead} />
          {content}
        </Stack>
      </Card>

      {view !== undefined && <AgentHistory view={view} limitWord={ABOUT[stage].limit} />}
    </Stack>
  );
}
