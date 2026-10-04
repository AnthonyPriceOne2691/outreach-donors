/**
 * Сохранение настроек агента: новая версия этапа и словами — что вышло.
 */

import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { saveAgentSettings } from '../api/agent';
import type { AgentSettingsBody } from '../api/agent';
import { refusalOf } from '../api/client';
import type { LetterStage } from '../api/types';

export const AGENT_KEY = ['agent-settings'] as const;

/** После сохранения черновик этапа снимается (`onSaved`): экран показывает
 *  сохранённое, а не набранное. */
export function useAgentSave(onSaved: (stage: LetterStage) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ stage, body }: { stage: LetterStage; body: AgentSettingsBody }) =>
      saveAgentSettings(stage, body),
    onSuccess: async (version, { stage }) => {
      onSaved(stage);
      await queryClient.invalidateQueries({ queryKey: AGENT_KEY });
      notifications.show({
        color: 'green',
        message: `Настройки агента сохранены как версия ${version.version}.`,
      });
    },
    onError: (failure) =>
      notifications.show({
        color: 'red',
        title: 'Настройки агента не сохранены',
        message: refusalOf(failure),
      }),
  });
}
