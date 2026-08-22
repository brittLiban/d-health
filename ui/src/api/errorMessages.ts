import type { ErrorCode } from './types'

// One canonical French message per error code (docs/UI-001-CLINICAL-UX.md §20),
// so no screen invents its own wording for the same failure.
export const ERROR_MESSAGES: Record<ErrorCode, { title: string; message: string }> = {
  VALIDATION_ERROR: {
    title: 'Requête invalide',
    message: "Les informations fournies n'ont pas pu être traitées.",
  },
  NOT_AUTHORIZED: {
    title: 'Accès refusé',
    message: "Vous n'êtes pas autorisé(e) à effectuer cette action.",
  },
  NOT_FOUND: {
    title: 'Introuvable',
    message: "La ressource demandée n'existe pas.",
  },
  CONFLICT: {
    title: 'Conflit',
    message: 'Cette action entre en conflit avec une donnée existante.',
  },
  NETWORK_ERROR: {
    title: 'Connexion impossible',
    message: 'Impossible de joindre le serveur. Vérifiez la connexion réseau.',
  },
  SERVER_ERROR: {
    title: 'Erreur du serveur',
    message: 'Une erreur est survenue. Réessayez dans un instant.',
  },
  AMBIGUOUS_WRITE: {
    title: 'État inconnu',
    message: "La dernière action n'a pas pu être confirmée.",
  },
}
